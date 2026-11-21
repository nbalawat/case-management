/** Whether and how a query result can be drawn. */
export type Result = { columns: string[]; types: string[]; rows: (string | number | boolean | null)[][] }

const NUMERIC = /^(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|FLOAT|DOUBLE|DECIMAL.*)$/

/** Which columns a chart can be drawn from: the first non-numeric column as the axis, up to three numeric columns as series. */
export function chartable(r: Result) {
  const label = r.types.findIndex(t => !NUMERIC.test(t))
  const values = r.types.map((t, i) => (NUMERIC.test(t) ? i : -1)).filter(i => i >= 0).slice(0, 3)
  return label >= 0 && values.length > 0 && r.rows.length > 1 && r.rows.length <= 400 ? { label, values } : null
}
