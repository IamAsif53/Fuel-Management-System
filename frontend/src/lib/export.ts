/**
 * Utility functions for exporting operational data to CSV and JSON formats.
 */

export function exportToCSV<T extends Record<string, any>>(
  filename: string,
  rows: T[],
  headers?: { key: keyof T; label: string }[]
): void {
  if (!rows || rows.length === 0) {
    alert("No data available to export.");
    return;
  }

  const cols = headers || Object.keys(rows[0]).map((k) => ({ key: k, label: k }));
  const headerLine = cols.map((c) => `"${String(c.label).replace(/"/g, '""')}"`).join(",");

  const lines = rows.map((row) =>
    cols
      .map((c) => {
        const val = row[c.key];
        if (val === null || val === undefined) return '""';
        if (typeof val === "object") return `"${JSON.stringify(val).replace(/"/g, '""')}"`;
        return `"${String(val).replace(/"/g, '""')}"`;
      })
      .join(",")
  );

  const csvContent = [headerLine, ...lines].join("\r\n");
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  downloadBlob(blob, filename.endsWith(".csv") ? filename : `${filename}.csv`);
}

export function exportToJSON<T>(filename: string, data: T): void {
  const jsonContent = JSON.stringify(data, null, 2);
  const blob = new Blob([jsonContent], { type: "application/json;charset=utf-8;" });
  downloadBlob(blob, filename.endsWith(".json") ? filename : `${filename}.json`);
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.setAttribute("href", url);
  link.setAttribute("download", filename);
  link.style.visibility = "hidden";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
