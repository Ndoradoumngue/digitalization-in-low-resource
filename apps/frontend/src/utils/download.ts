/** Save a Blob as a file download in the browser. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  // The anchor must be attached to the DOM for .click() to reliably
  // trigger a download in every browser (Safari in particular ignores
  // clicks on detached elements). Revoking the object URL must also
  // be deferred - doing it synchronously right after click() can race
  // with the browser actually starting the download and silently
  // kill it before any bytes are read.
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
