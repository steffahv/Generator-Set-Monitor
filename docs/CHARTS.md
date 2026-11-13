# Historical charts: implementation and data rules

## Purpose and entry point

The charts view provides regional trends and raw snapshot comparisons alongside the operational dashboard:

1. Select **Charts & comparison** in the application header.
2. Choose a metric, grouping, reference date, and optional comparison window.
3. Use **Back to dashboard** to return to the main view.

The frontend is in `public/index.html`; the endpoint and calculations are in `app/main.py`. Trend and comparison data come from `public.generator_daily_history`. The chart endpoint does not read `generator_d0`, `generators`, or uploaded Excel records. `public.region_historical_baseline` supplements periods without operational cells; the table and its required columns must exist, even if it is empty. No chart-specific table is created.

Chart.js and Chart.js DataLabels load from jsDelivr.

## Controls and actions

| Control | Values | Effect |
| --- | --- | --- |
| Metric | Running hours, Number of starts | Selects the cumulative counter used in the trend and comparison. |
| Group by | Month, Week | Selects monthly rollup or ISO-week periods for the trend. |
| Compare snapshots | None, DoD, WoW, 6D Back, MoM | Shows or hides the grouped-bar comparison of raw totals. |
| Reference date | Any date | Limits history to this date. Blank chooses the latest relevant operational/baseline period. |
| Refresh charts | Button | Requests the endpoint using the selected controls. |

The trend has **Download PNG** and **Copy chart image** actions. Copying requires browser clipboard support in a secure context such as HTTPS or localhost. Its value panel updates on hover; clicking a plot position pins that period's values, and clicking the same period again clears the pin. The comparison bar chart has a PNG download.

## API contract

The frontend calls:

```text
GET /analytics/generator-trends
    ?metric=running_hours
    &granularity=month
    &reference_date=2026-09-30
    &comparison_period=WoW
```

Parameters:

- `metric`: `running_hours` or `num_starts`.
- `granularity`: `month` or `week`.
- `reference_date`: optional ISO date (`YYYY-MM-DD`).
- `comparison_period`: `none`, `DoD`, `WoW`, `6DayBack`, or `MoM`.

Unsupported values return HTTP 400. Missing `region_historical_baseline` or required columns returns HTTP 503. With no usable history and no baseline rows, the endpoint returns empty chart arrays.

Response fields:

- `labels`: monthly view includes periods from May 2026 (up to 12); weekly view includes only the latest four ISO weeks through the effective reference date.
- `sites`: four `{name, values}` regional series (the `sites` key is retained for frontend compatibility): `San Martin`, `Arequipa`, `La Libertad`, and `Ancash`.
- `total`: horizontal sum of the four regional period values.
- `accumulated`: monthly cumulative total or weekly total accumulated within the calendar month when each ISO week starts.
- `regional_accumulated`: per-region counterpart to `accumulated`, using the same monthly continuous or weekly calendar-month rule.
- `site_values`: site-level operational deltas for the latest visible period for the requested metric. Baseline cells have no site-level breakdown and are not represented here.
- `comparison`: `null` when comparison is off; otherwise actual selected snapshot dates, region names, and raw previous/current metric totals.
- `baseline_cells_used`: number of missing operational `(period, region)` cells supplied by baseline data across the full timeline.
- `period_sources`: source label (`operational`, `baseline`, or `empty`) for each visible period and region.

## Database and identity rules

The trend SQL reads `public.generator_daily_history` and left-joins `public.sites` using:

```sql
lower(trim(s.site_name)) = lower(trim(h.site_name))
```

When a site name matches, the local `sites.site_id` and its region are preferred. Otherwise the query falls back to the source row's `site_id` and region. Ensure names map uniquely and consistently; source and local IDs may belong to different databases. The frontend/database integration does not create site records during chart reads.

Only rows where `device_type = 'generator'` and `snapshot_date` is present are candidates. Rows are deduplicated by:

```text
(mapped site_id, device_type, device_index, snapshot_date)
```

The row with the latest `captured_at` wins (with source `site_id` as a tie-breaker). The same device identity `(site_id, device_type, device_index)` is used for counter differences over time. Keep this identity aligned with the source history primary key.

The endpoint maps region codes and names to the four canonical labels. It first uses `region`, then attempts to infer a region from `site_name`. Unrecognized rows are omitted from regional sums and written to the application log.

`region_historical_baseline` requires:

| Column | Use |
| --- | --- |
| `period_type` | `month`/`monthly` or `week`/`weekly` |
| `period_label` | Calendar month or ISO week label parsed by the backend |
| `region_name` | Canonical region name or supported alias |
| `metric_name` | Running-hours or starts metric identifier |
| `delta_value` | Precomputed regional period variance |

Monthly labels accepted include `Feb-26`, `Feb 2026`, and `2026-02`. Weekly labels should include the ISO year, such as `Week 33 (2026)` or `2026-W33`. A week label without a year is interpreted using the reference ISO year and is unsafe for multi-year baselines.

## Operational counter deltas

`running_hours` and `num_starts` are cumulative counters. PostgreSQL applies `LAG(metric)` per generator identity, ordered by `snapshot_date`, after filtering out null metric values. Each non-null reading contributes:

```text
period variance = current reading - previous available reading
```

- The first available non-null reading contributes zero because it has no prior value.
- If the counter decreases, it is treated as a reset and the current value is used as the variance since reset.
- A null metric row is excluded from the `LAG` input; it is not treated as zero. The next available non-null reading is compared with the preceding available non-null reading. If snapshots were missed, that multi-day change is assigned to the later reading's week.
- The application does not interpolate missing snapshots.

Weekly grouping uses the weekly calculations described below. Monthly grouping is calculated separately at calendar-month boundaries: for each generator, it subtracts the latest available counter reading on or before the previous month's final calendar day from the latest reading on or before the current month's final day (capped at the effective reference date for the current month). This avoids assigning a whole cross-month week to one month. If a counter resets between those readings, the current reading is used as the month's variance. A generator without readings on both sides of the boundary does not contribute an operational monthly delta.

### Weekly calculation cutover

Weeks before the cutover on **2026-10-19 (Week 43)** keep the legacy calendar-week `LAG` calculation. Legacy weeks only include snapshots through **2026-10-16**; later snapshots from Oct 17–18 do not revise Week 42. The published Week 42 running-hours value also has a fixed Ancash value of **132.5** to retain the report as it was issued; replaying the current database snapshots produced 132.8 for this cell. Starting Week 43, each weekly value is calculated from the snapshot on the effective reference date's weekday in that ISO week minus the snapshot exactly seven days earlier, matching the main comparison's WoW calculation. Monthly totals use these same preserved legacy weekly values and post-cutover comparison values.

For example, a reference date of October 30, 2026 (Friday) selects October 23 minus October 16 for Week 43, and October 30 minus October 23 for Week 44. Later weekend captures do not replace these Friday reporting dates. This weekday alignment applies to the weekly chart for both metrics. Monthly totals do not roll up these weekly values; each monthly point uses its own calendar-boundary readings.

### Calendar-month boundaries

Monthly values use calendar dates, not week-to-month assignment. For a reference date of November 6, the November point compares each generator's latest available reading on or before November 6 with its latest reading on or before October 31. If the October 31 capture is absent, the latest earlier reading is used as the available boundary reading; the chart does not interpolate a missing capture. As a result, a monthly value and a weekly value need not match when their comparison intervals have different start/end dates. For example, the weekly October 30–November 6 comparison includes dates from October, while the November monthly point is anchored at the October 31 calendar boundary.

### Baseline merge, totals, and accumulation

The backend merges operational and baseline values per period and region:

1. Use the operational cell when operational data produced that `(period, region)` cell.
2. Otherwise use the matching baseline `delta_value`.
3. If neither source has the cell, the chart's reindexed regional value is zero.

Operational and baseline values are not added together for one cell. This is a cell-level fallback: a September operational value for one region does not prevent a baseline value from filling a missing September cell for another region.

`GE Total hours` or `GE Total starts` is the sum of the four regional period values. Missing cells are initialized to zero in the chart dataframe and JSON arrays; the endpoint does not preserve absent period/region cells as `null`.

- **Monthly accumulated:** cumulative sum of calendar-month GE totals through the full available timeline. A September point therefore includes January through September, not September alone.
- **Weekly accumulated:** cumulative sum of weekly GE totals within the calendar month when each ISO week starts. A week crossing a month boundary belongs to the month containing its Monday.

Monthly accumulation is calculated over the full available timeline before the chart is sliced to its latest 12 labels, so earlier months still contribute to the displayed cumulative values. Weekly accumulation also uses the full available timeline and resets at calendar-month boundaries based on each ISO week's Monday. The chart still displays only the latest four weeks, but the accumulated table value includes earlier weeks from that month even when they are outside the visible chart window.

## Snapshot comparison and missing coverage

Snapshot comparison uses raw counter totals; it is separate from the period-delta calculation:

1. The active date is the latest operational snapshot on or before the effective reference date.
2. The backend offsets that date by DoD (−1 day), WoW (−7 days), 6D Back (−6 days), or MoM (−1 month).
3. For the prior side, it selects the latest available snapshot on or before that target date.
4. It sums non-null metric values by region at each selected snapshot.

The response includes the actual dates selected. Missing devices are not matched across the two sides and are not imputed: each regional total includes only the devices with a usable metric in that snapshot. Unequal coverage can therefore make raw current and previous totals look different even when some of that difference comes from missing devices. Check the capture coverage before interpreting snapshot totals as variance. The comparison chart displays the two totals; it does not calculate device-level VAR.

## Regional summary and site list

The charts view makes a second request for the other counter so it can show both metrics regardless of which metric is plotted. The selected metric response draws the line chart and initial summary immediately; the companion response then fills any values sourced from chart periods.

- **Genset turned on (Num Starts):** the regional `num_starts` period variance for the latest displayed period.
- **Running Hours:** the regional `running_hours` period variance for that period.
- **Accumulated:** per-region running-hours accumulation using the chart's grouping rule. For weekly charts it is month-to-date, grouped by the month containing the week's Monday; the total row is the sum of the regional values.
- **Sites with Running Hrs VAR > 10:** up to ten operational sites sorted by descending site-level `running_hours` variance for that same period. The `Times` column shows the site's `num_starts` variance when available.

When the main dashboard has a comparison period selected, the regional `num_starts` and `running_hours` VARs and the site ranking use the same selected snapshot pair and `computeComparisonDelta()` calculation as the main comparison table. Site values are grouped by `site_id`; the ranking includes sites with running-hours VAR greater than 10. Weeks before the **2026-10-19 (Week 43)** cutover retain the legacy ISO-week LAG calculation using snapshots through Oct 16; Week 42's published running-hours Ancash cell is held at 132.5. From Week 43 onward, each weekly point uses the snapshot on the effective reference date's weekday in that ISO week minus the snapshot exactly seven days earlier, matching the main table's WoW arithmetic. Monthly values use calendar-boundary readings, independently of weekly values. Accumulated values are calculated from the resulting timeline. The summary fallback and baseline rules are unchanged.

## Frontend implementation map

Relevant code in `public/index.html`:

- `openAnalyticsButton`, `backToMainButton`: switch views.
- `analyticsMetric`, `analyticsGranularity`, `analyticsComparison`, `analyticsReferenceDate`: request controls.
- `loadAnalyticsCharts()`: builds the request, updates status, and handles empty/error responses.
- `renderTrendChart(data)`: creates regional, total, and accumulated series and the pinned value panel.
- `renderAnalyticsSummary(chartData, companionData)`: combines both metric responses into the latest-period regional summary and operational site list.
- `renderComparisonChart(comparison, metricLabel)`: renders previous/current grouped bars.
- `downloadChartImage(chart, filename)`: exports a chart as PNG.
- `copyChartImage(chart)`: copies the trend canvas PNG to the clipboard.

## Performance and request path

The main dashboard table and the charts endpoint do different work, although both ultimately use the same history source. The dashboard normally requests one selected snapshot, about one row per generator (448 rows in the reported environment), and applies its interactive filters to that result. The trend endpoint must read historical readings for all devices so it can deduplicate snapshots, compare each cumulative counter with the preceding available non-null reading, handle counter resets, and aggregate the resulting deltas by week/month, region, and site. It also calculates cumulative series and optional snapshot comparisons. A latest-snapshot table cannot supply those historical deltas by itself.

The trend request currently has two substantial data paths per metric:

1. It reads the deduplicated history into the application to determine periods, comparison data, and chart aggregation.
2. It runs a separate PostgreSQL query with `LAG` to produce operational weekly deltas and site-level values.

This preserves the established calculation rules, but repeats work over roughly 73,000 history rows in the reported environment. When both metrics are needed, the browser requests them separately; the second request is for the summary and site list. Each database response may be small while query execution and server-side transformation still take several seconds.

The application caches the selected metric's history rows in process memory for 60 seconds. Monthly/weekly changes can reuse that history while the cache is valid, but the live `LAG` query and transformations still run. The cache is local to one application process, is not shared across workers or restarts, and the Refresh charts action bypasses it (`force_refresh=true`). Timing logs under `generator_trends` report `history_read` (including `cache_hit`), `history_transform`, `baseline_read`, `live_read`, `live_transform`, and total request time.

In the latest reported use, chart response time improved from about 12 seconds to 5–6 seconds. The remaining delay is consistent with the historical queries and per-request processing described above; it is not caused by Chart.js drawing the lines. The most direct next optimization is to avoid scanning and transforming the same history twice per metric while keeping the current delta, reset, baseline, and aggregation rules unchanged. Any query/materialized-summary change should be checked against the existing chart values before rollout.

## Maintenance notes

- Add a selectable metric to both the frontend selector and backend whitelist. Counter metrics can use the existing difference logic; instantaneous metrics require a separate aggregation rule.
- If source identity, deduplication, or site-name mapping changes, update this document and the dashboard identity rules together.
- If comparison windows change, update endpoint validation, date offsets, and frontend options together.
- Keep baseline period labels and names aligned with the parser and canonical regions. Operational values take precedence only for the same period/region cell.
- Preserve the PostgreSQL `LAG` counter-delta behavior, including null handling and resets, when optimizing the endpoint.
- If changing query shape or adding pre-aggregation, compare monthly/weekly regional values, accumulated series, baselines, site ranking, and snapshot comparisons against the current implementation.
