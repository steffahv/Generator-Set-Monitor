# Historical charts: implementation guide

## Purpose and entry point

The charts feature provides a historical view of generator counter readings without replacing the operational table. It is a second view in the existing single-page frontend:

1. Select **Charts & comparison** in the application header.
2. Configure the metric, grouping, reference date, and optional snapshot comparison.
3. Use **Back to dashboard** to return to the generator table and upload sections.

The frontend is in `public/index.html`; the data endpoint and pandas aggregation are in `app/main.py`. No new database tables are introduced. Chart.js is loaded from jsDelivr, so the browser needs access to that CDN to render charts.

## User controls

| Control | Values | Effect |
| --- | --- | --- |
| Metric | Running hours, Number of starts | Selects the cumulative counter used for both charts. |
| Group by | Month, Week | Buckets counter changes by calendar month or ISO week. |
| Compare snapshots | None, DoD, WoW, 6D Back, MoM | Shows or hides a second chart comparing snapshot totals by site. |
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

Unsupported metric, granularity, or comparison period returns HTTP 400. An empty database or a date range without readings returns empty chart arrays rather than fabricated data.

The response contains:

- `labels`: the latest 12 month or week labels through the reference date.
- `sites`: one `{name, values}` series per site.
- `total`: sum of site period values for each label.
- `accumulated`: cumulative sum of `total` across the available history through the reference date. It is calculated before the 12-label display window is sliced.
- `comparison`: `null` when comparison is off; otherwise, site labels, actual snapshot dates, and current/previous values for the selected metric.

## Backend data flow and rules

`generator_trends` queries `Generator` rows joined to `Site` and uses the canonical `Site.site_name`. It filters out rows without `snapshot_date` or a numeric value for the selected metric, then prepares the records in pandas.

### Deduplication

An imported file can create repeated rows for the same generator and snapshot date. The endpoint sorts records by snapshot date and `generator_id`, then keeps the last row for each:

```text
(site_id, router_ip, reg, ip, snapshot_date)
```

The highest `generator_id` is treated as the most recently inserted row for that identity and date. Keep this key aligned with the generator identity used by the main comparison logic if that logic changes.

### Period trend values

`running_hours` and `num_starts` are treated as cumulative counters. For each generator identity, the endpoint sorts readings by date and calculates:

```text
period_value = current reading - previous reading
```

The first known reading contributes zero because there is no earlier value from which to calculate usage. When a counter decreases, the code treats that as a reset and uses the new reading as the value since reset. Each difference is assigned to the month or ISO week containing the later snapshot date.

Pandas then sums those values by `(period, site)`. `GE total` is the sum across sites per period. `Accumulated` is the running sum of those period totals. The accumulated series covers all available periods through the reference date even though the trend chart displays only the latest 12 labels.

### Snapshot comparison values

Comparison uses raw counter totals, rather than the period differences used in the trend chart:

1. The active snapshot is the latest available snapshot on or before `reference_date` (or the latest snapshot when no date is supplied).
2. The endpoint offsets that date by the chosen comparison window: DoD −1 day, WoW −7 days, 6D Back −6 days, or MoM −1 month.
3. For each side, it selects the latest snapshot on or before its target date.
4. It sums the chosen metric by site at each selected snapshot.

The response reports the actual snapshot dates found. If there is no snapshot at or before the previous target, the comparison chart is omitted and the frontend reports that no previous snapshot was available.

## Frontend rendering and visual structure

The charts view is part of the same HTML document, not a separate route. `mainView` contains the operational dashboard, while `analyticsView` contains the historical controls and charts. The header shortcut hides one view and shows the other.

The trend uses a responsive Chart.js line chart:

- Each site is a separate line, assigned colors from a fixed palette.
- `GE total` is a solid blue line.
- `Accumulated` is a dashed green line.
- The legend is below the plot; hovering shares a tooltip across the same period.
- The vertical axis starts at zero and names the selected metric.
- Month labels use `Mon-YY`; week labels include the ISO week and year to disambiguate year boundaries.

When comparison is enabled and data exists, the second chart is a grouped bar chart. It displays previous snapshot totals in light blue and current snapshot totals in blue, with one category per site. Its title includes the actual dates selected by the backend.

The chart cards use responsive containers. PNG downloads use each Chart.js instance's `toBase64Image()` output; trend filenames include metric and grouping, while comparison filenames include the metric.

## Frontend implementation map

Relevant elements and functions in `public/index.html`:

- `openAnalyticsButton`, `backToMainButton`: switch between dashboard and charts view.
- `analyticsMetric`, `analyticsGranularity`, `analyticsComparison`, `analyticsReferenceDate`: request controls.
- `loadAnalyticsCharts()`: builds query parameters, fetches the endpoint, updates the status, and handles empty/error states.
- `renderTrendChart(data)`: creates the site, total, and accumulated line datasets.
- `renderComparisonChart(comparison, metricLabel)`: builds the previous/current grouped bars.
- `downloadChartImage(chart, filename)`: downloads the selected chart as PNG.

Chart.js is included before the inline application script using the jsDelivr CDN. If the CDN is unavailable, the charts view shows an explanatory status instead of trying to construct a chart.

## Changes and maintenance

- Add any new selectable metric to both the frontend selector and the backend `metric_labels` whitelist. Only cumulative counters can use the current difference calculation without further changes.
- If adding an instantaneous metric (for example, fuel percentage or voltage), define whether each period should use average, minimum/maximum, or the last reading. Do not apply counter differences to it.
- If changing generator identity or duplicate resolution, update the deduplication key and keep the main table's comparison identity consistent.
- If changing the comparison windows, update the API validation, target-date calculation, and frontend options together.
- The endpoint currently loads all matching snapshot rows into pandas per request. For a much larger history, consider pushing deduplication/aggregation into SQL or caching the prepared series.
- Since period differences are assigned to the date of the later snapshot, a long gap between imports places the whole change in that later month/week. The chart does not interpolate missing snapshots.
