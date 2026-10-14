/** CSV export of result rows, safe to open in a spreadsheet. */

function cell(value) {
  if (value === null || value === undefined) return "";
  let text = String(value);
  // A text cell starting with = + - @ would run as a formula in Excel or Sheets; numbers are left alone.
  if (typeof value === "string" && /^[=+\-@]/.test(text)) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function toCsv(rows) {
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))];
  return [columns.map(cell).join(","), ...rows.map((row) => columns.map((c) => cell(row[c])).join(","))].join("\r\n");
}

export function downloadCsv(rows, name) {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${name.replace(/[^a-z0-9_-]+/gi, "-")}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
