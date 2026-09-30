/* API-1 pure ViewSpec; the maintained host escapes every visible string. */
export function render({metric, config}) {
  const unit = config.displayUnit;
  const value = metric.value === null ? null : Math.round(metric.value * (unit === 'lb' ? 2.2046226218 : 1) * 10) / 10;
  return {
    schemaVersion: 1,
    title: config.title,
    rows: [
      {label: 'Mean body mass', value, unit},
      {label: 'Recorded observations', value: metric.count, unit: null},
      {label: 'Source', value: metric.sourceId, unit: null}
    ],
    status: metric.missingness === 'insufficient_data' ? 'No measurements in this selected window.' : 'Seven local calendar days · ' + metric.freshness + (metric.truncated ? ' · limited view' : '')
  };
}
