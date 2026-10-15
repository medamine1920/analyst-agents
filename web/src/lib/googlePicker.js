/**
 * Google's file picker: the user chooses, in a Google window, which Drive files this console may read.
 * The console then holds access to those files only (the drive.file permission), never the whole Drive.
 * Google's script is loaded the first time the picker opens, not with the page.
 */

const SPREADSHEETS = [
  "application/vnd.google-apps.spreadsheet",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/csv",
].join(",");

let loading = null;

function loadPicker() {
  if (window.google?.picker) return Promise.resolve();
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://apis.google.com/js/api.js";
    script.async = true;
    script.onload = () => window.gapi.load("picker", { callback: resolve, onerror: () => reject(new Error("Google's file picker could not be loaded.")) });
    script.onerror = () => {
      loading = null;
      reject(new Error("Google's file picker could not be loaded. Check your connection and try again."));
    };
    document.head.appendChild(script);
  });
  return loading;
}

/** Opens the picker; resolves with the chosen files ([{ id, name }]), or [] if the user closes it. */
export async function pickGoogleFiles({ access_token: token, api_key: key, app_id: appId }) {
  await loadPicker();
  const g = window.google.picker;
  return new Promise((resolve) => {
    const view = new g.DocsView(g.ViewId.DOCS).setMimeTypes(SPREADSHEETS).setMode(g.DocsViewMode.LIST);
    new g.PickerBuilder()
      .addView(view)
      .enableFeature(g.Feature.MULTISELECT_ENABLED)
      .setOAuthToken(token)
      .setDeveloperKey(key)
      .setAppId(appId)
      .setTitle("Choose the files Analyst Console may read")
      .setCallback((data) => {
        if (data.action === g.Action.PICKED) resolve(data.docs.map((d) => ({ id: d.id, name: d.name })));
        else if (data.action === g.Action.CANCEL) resolve([]);
      })
      .build()
      .setVisible(true);
  });
}
