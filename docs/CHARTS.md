# Historical charts: implementation guide

## Purpose and entry point

The charts feature provides a historical view of generator counter readings without replacing the operational table. It is a second view in the existing single-page frontend:

1. Select **Charts & comparison** in the application header.
2. Configure the metric, grouping, reference date, and optional snapshot comparison.
3. Use **Back to dashboard** to return to the generator table and upload sections.

The frontend is in `public/index.html`; the endpoint, SQL counter-delta query, and response assembly are in `app/main.py`. The endpoint reads `generators`, `sites`, and `region_historical_baseline`; it does not create a new analytics table. Chart.js and its DataLabels plugin load from jsDelivr.

## User controls

| Control | Values | Effect |
| --- | --- | --- |
| Metric | Running hours, Number of starts | Selects the cumulative counter used for both charts. |
| Group by | Month, Week | Buckets counter changes by calendar month or ISO week. |
| Compare snapshots | None, DoD, WoW, 6D Back, MoM | Shows or hides a second chart comparing snapshot totals by region. |
| Reference date | Any date | Uses data on or before that date. Blank means latest available snapshot. |
| Refresh charts | Button | Re-fetches data using the current controls. |

Both charts have a **Download PNG** button. The trend chart is available when there is trend data; the comparison chart appears only when comparison is enabled and a prior snapshot can be found.

## Request and response contract

The frontend calls:

```text
GET /analytics/generator-trends
    ?metric=running_hours
    &granularity=month
    &reference_date=2026-09-24
    &comparison_period=WoW
```

Parameters:

- `metric`: `running_hours` or `num_starts`.
- `granularity`: `month` or `week`.
- `reference_date`: optional ISO date (`YYYY-MM-DD`).
- `comparison_period`: `none`, `DoD`, `WoW`, `6DayBack`, or `MoM`.

Unsupported metric, granularity, or comparison period returns HTTP 400. If `region_historical_baseline` or one of its required columns is unavailable, the endpoint returns HTTP 503 with a schema-oriented error. An empty database or a date range without readings and without baseline rows returns empty chart arrays rather than fabricated data.

The response contains:

- `labels`: the latest 12 month or week labels through the reference date.
- `sites`: one `{name, values}` series for each of the four canonical regions: `San Martin`, `Arequipa`, `La Libertad`, and `Ancash`. The key remains named `sites` for compatibility with the chart renderer.
- `total`: horizontal sum of the four regional values for each period.
- `accumulated`: cumulative total. Monthly charts accumulate across the entire available history through the reference date. Weekly charts restart the accumulated value when the week-start month changes. Both are calculated before the 12-label display window is sliced.
- `comparison`: `null` when comparison is off; otherwise, regional labels, actual snapshot dates, and current/previous raw counter totals.
- `baseline_cells_used`: number of missing `(period, region)` cells filled from the historical baseline.
- `period_sources`: for each displayed period and region, identifies whether the value came from `operational`, `baseline`, or neither (`empty`).

## Backend data flow and rules

`generator_trends` reads `Generator` rows joined to `Site`, including `Site.region` and `Site.site_name`. PostgreSQL calculates live counter deltas with window functions. Python maps those rows to canonical regions, combines them with the manual baseline, and assembles the timeline. The same deduplicated snapshot rows are prepared in pandas for the raw-snapshot comparison chart.

The chart grouping key is the canonical region rather than the individual site name. The endpoint first maps `Site.region`; if the field is blank or unrecognized, it infers the region from `site_name`. It recognizes the full names and common codes `SM`, `AR`, `LL`, and `AN`, and normalizes accents and casing. Sites that do not map to one of the four target regions are skipped and written to the application log. Startup also adds the nullable `sites.region` column to older databases if it is missing.

### Deduplication

An import creates generator records for its snapshot date. Re-uploading the same file/date can therefore create repeated rows. Both the trend query and the comparison preparation keep the greatest `generator_id` for each:

```text
(site_id, router_ip, reg, ip, snapshot_date)
```

The highest `generator_id` is treated as the most recently inserted row for that identity and date. Keep this key aligned with the generator identity used by the main comparison logic if that logic changes.

### Period trend values

`running_hours` and `num_starts` are cumulative counters. After deduplication, PostgreSQL calculates `LAG(metric)` for each generator identity ordered by `snapshot_date`. Each reading contributes the change since that generator's previous snapshot, assigned to the month or ISO week containing the later snapshot:

```text
period_value = current reading - previous reading
```

The first known reading contributes zero because it has no previous value for comparison. If the counter decreases, the code treats that as a reset and uses the new reading as usage since reset. Missing days do not cause repeated addition of the counter: the next reading is compared with the preceding available snapshot, and that difference is assigned once to the later snapshot's period.

The endpoint maps each row to its canonical region and sums deltas by `(period, region)`. Source selection is cell-by-cell:

1. Use the operational sum when that period and region have generator readings.
2. Otherwise, use the matching `delta_value` from `region_historical_baseline`.
3. Use zero only when neither source has that period/region cell.

The baseline columns are `period_type`, `period_label`, `region_name`, `metric_name`, and `delta_value`. Operational and baseline values are never added together for the same cell. `period_sources` identifies the selected source for each displayed period and region; `baseline_cells_used` counts baseline cells across the complete timeline, not just September or the currently visible data source.

For the September 2026 data reviewed during this change, the manual baseline rows total **429.6** running hours, while the operational deltas from daily generator snapshots total about **460.068**. Because all four regions have operational values for September, the chart uses those operational values and ignores the September baseline cells. The API previously returned an incorrect value near **74,572**. The trend calculation was moved to an explicit PostgreSQL `ROW_NUMBER()` + `LAG()` query matching the validated SQL calculation; the corrected chart now shows the operational result rather than accumulating full daily readings.

Month and week periods from operational data and baseline data are merged into one continuous timeline. The endpoint reindexes that range; any period/region cell absent from both sources is zero. `GE Total hours` (or `GE Total starts`) is the horizontal sum across the four regions per period. The monthly `Accumulated` series is a continuous running sum; the weekly series resets by the month containing each week's Monday start. The calculations cover the complete merged history through the reference date even though the trend chart displays only the latest 12 labels.

Monthly baseline labels can use labels such as `Feb-26` or `2026-02`. Weekly labels can include a year (for example, `Week 33 (2026)` or `2026-W33`). If a weekly label omits its year, the endpoint interprets it in the ISO year of the reference date; include a year in `period_label` when the baseline spans multiple years.

### Snapshot comparison values

Comparison uses raw counter totals, rather than the period differences used in the trend chart:

1. The active snapshot is the latest available snapshot on or before `reference_date` (or the latest snapshot when no date is supplied).
2. The endpoint offsets that date by the chosen comparison window: DoD −1 day, WoW −7 days, 6D Back −6 days, or MoM −1 month.
3. For each side, it selects the latest snapshot on or before its target date.
4. It sums the chosen metric by region at each selected snapshot.

The response reports the actual snapshot dates found. If there is no snapshot at or before the previous target, the comparison chart is omitted and the frontend reports that no previous snapshot was available.

## Frontend rendering and visual structure

The charts view is part of the same HTML document, not a separate route. `mainView` contains the operational dashboard, while `analyticsView` contains the historical controls and charts. The header shortcut hides one view and shows the other.

The trend uses a responsive Chart.js line chart with Chart.js DataLabels:

- Each of the four regions is a separate line, assigned colors from a fixed palette.
- `GE Total hours` (or `GE Total starts`) is a solid blue line.
- `Accumulated` is a dashed green line.
- `Accumulated` uses a separate right-side scale so its cumulative magnitude does not determine the scale for the regional period deltas.
- Both vertical scales start at zero and add headroom (`8%` for period deltas, `10%` for accumulated). Extra top and right chart padding keeps labels near the final point inside the canvas.
- Nonzero points show their values directly on the chart; overlapping labels are automatically suppressed and remain available in the tooltip.
- The legend contains six lines and sits below the plot; hovering shares a tooltip across the same period.
- The vertical axis starts at zero and names the selected metric.
- Month labels use `Mon-YY`; week labels include the ISO week and year to disambiguate year boundaries.

When comparison is enabled and data exists, the second chart is a grouped bar chart. It displays previous snapshot totals in light blue and current snapshot totals in blue, with one category per region. Its title includes the actual dates selected by the backend.

The chart cards use responsive containers. PNG downloads use each Chart.js instance's `toBase64Image()` output; trend filenames include metric and grouping, while comparison filenames include the metric.

## Frontend implementation map

Relevant elements and functions in `public/index.html`:

- `openAnalyticsButton`, `backToMainButton`: switch between dashboard and charts view.
- `analyticsMetric`, `analyticsGranularity`, `analyticsComparison`, `analyticsReferenceDate`: request controls.
- `loadAnalyticsCharts()`: builds query parameters, fetches the endpoint, updates the status, and handles empty/error states.
- `renderTrendChart(data)`: creates the site, total, and accumulated line datasets.
- `renderComparisonChart(comparison, metricLabel)`: builds the previous/current grouped bars.
- `downloadChartImage(chart, filename)`: downloads the selected chart as PNG.

Chart.js and Chart.js DataLabels are included before the inline application script using jsDelivr. Chart.js must load for the charts view to work; if the DataLabels plugin alone is unavailable, the chart still renders but point labels are omitted.

## Changes and maintenance

- Add any new selectable metric to both the frontend selector and the backend `metric_labels` whitelist. Only cumulative counters can use the current difference calculation without further changes. Add matching `metric_name` values to the baseline table as needed.
- If adding an instantaneous metric (for example, fuel percentage or voltage), define whether each period should use average, minimum/maximum, or the last reading. Do not apply counter differences to it.
- If changing generator identity or duplicate resolution, update the deduplication key and keep the main table's comparison identity consistent.
- If changing the comparison windows, update the API validation, target-date calculation, and frontend options together.
- Keep baseline `period_type`, `period_label`, `region_name`, `metric_name`, and `delta_value` consistent with the parser and the four canonical region names. Operational values take priority over baseline values for the same period and region.
- The endpoint reads the snapshot history for both the SQL trend calculation and the pandas raw-snapshot comparison. For a much larger history, consider reducing comparison rows in SQL or caching the prepared series.
- Since period differences are assigned to the date of the later snapshot, a long gap between imports places the whole change in that later month/week. The chart does not interpolate missing snapshots.
