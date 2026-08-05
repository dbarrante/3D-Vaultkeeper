import React, { useState, useEffect } from "react";
import {
  Check,
  ChevronLeft,
  EthernetPort,
  Globe,
  KeyRound,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import {
  api,
  resolveApiOrigin,
  ALL_SLICER_TYPES,
  getEnabledLaunchSlicers,
  setEnabledLaunchSlicers,
  SLICERS,
  SlicerType,
} from "../services/api";
import WatcherInbox from "./WatcherInbox";

interface SettingsProps {
  onBack: () => void;
}

interface SlicerConfig {
  name: string;
  protocol: string;
}

const Settings: React.FC<SettingsProps> = ({ onBack }) => {
  const [apiPortStatus, setApiPortStatus] = useState(false);
  const [makerWorldTokenConfigured, setMakerWorldTokenConfigured] =
    useState(false);
  const [makerWorldToken, setMakerWorldToken] = useState("");
  const [makerWorldTokenStatus, setMakerWorldTokenStatus] = useState<
    "idle" | "saved" | "cleared" | "error"
  >("idle");
  const [showMakerWorldTokenHelp, setShowMakerWorldTokenHelp] =
    useState(false);
  const [openRouterKeyConfigured, setOpenRouterKeyConfigured] =
    useState(false);
  const [openRouterKey, setOpenRouterKey] = useState("");
  const [openRouterKeyStatus, setOpenRouterKeyStatus] = useState<
    "idle" | "saved" | "cleared" | "error"
  >("idle");
  const [launchSlicers, setLaunchSlicers] = useState<SlicerType[]>(() =>
    getEnabledLaunchSlicers(),
  );
  // Initialize state directly from localStorage to prevent flash
  const [selectedSlicer, setSelectedSlicer] = useState<SlicerType>(() => {
    const saved = localStorage.getItem("stlvault-slicer");
    return saved && saved in SLICERS ? (saved as SlicerType) : "orcaslicer";
  });

  const [selectedApiPort, setSelectedApiPort] = useState<string>(() => {
    const envport = resolveApiOrigin();
    const port = localStorage.getItem("api-port-override");
    if (port) {
      setApiPortStatus(true);
    }
    return port ? port : envport;
  });

  // Save slicer preference to localStorage when changed
  const handleSlicerChange = (slicer: SlicerType) => {
    const next = launchSlicers.includes(slicer)
      ? launchSlicers.filter((item) => item !== slicer)
      : [...launchSlicers, slicer];
    const safeNext = next.length ? next : [slicer];
    setLaunchSlicers(safeNext);
    setSelectedSlicer(safeNext[0]);
    setEnabledLaunchSlicers(safeNext);
  };

  useEffect(() => {
    api
      .getMakerWorldTokenStatus()
      .then((status) => setMakerWorldTokenConfigured(status.configured))
      .catch(() => setMakerWorldTokenStatus("error"));
    api
      .getOpenRouterKeyStatus()
      .then((status) => setOpenRouterKeyConfigured(status.configured))
      .catch(() => setOpenRouterKeyStatus("error"));
  }, []);

  const handleMakerWorldTokenSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!makerWorldToken.trim()) return;

    try {
      const status = await api.updateMakerWorldToken(makerWorldToken.trim());
      setMakerWorldTokenConfigured(status.configured);
      setMakerWorldToken("");
      setMakerWorldTokenStatus("saved");
    } catch (error) {
      setMakerWorldTokenStatus("error");
    }
  };

  const handleMakerWorldTokenClear = async () => {
    try {
      const status = await api.clearMakerWorldToken();
      setMakerWorldTokenConfigured(status.configured);
      setMakerWorldToken("");
      setMakerWorldTokenStatus("cleared");
    } catch (error) {
      setMakerWorldTokenStatus("error");
    }
  };

  const handleOpenRouterKeySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!openRouterKey.trim()) return;

    try {
      const status = await api.updateOpenRouterKey(openRouterKey.trim());
      setOpenRouterKeyConfigured(status.configured);
      setOpenRouterKey("");
      setOpenRouterKeyStatus("saved");
    } catch (error) {
      setOpenRouterKeyStatus("error");
    }
  };

  const handleOpenRouterKeyClear = async () => {
    try {
      const status = await api.clearOpenRouterKey();
      setOpenRouterKeyConfigured(status.configured);
      setOpenRouterKey("");
      setOpenRouterKeyStatus("cleared");
    } catch (error) {
      setOpenRouterKeyStatus("error");
    }
  };

  // Save API port preference to localStorage when changed
  const handleApiPortChange = (port: string) => {
    setSelectedApiPort(port);
  };

  const handleApiForm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedApiPort) return;

    localStorage.setItem("api-port-override", selectedApiPort);
    setApiPortStatus(true);
  };

  return (
    <div className="flex-1 p-4 sm:p-8 h-full overflow-y-auto relative flex flex-col">
      {/* Header Section */}
      <div className="flex flex-col gap-6 mb-8">
        <div className="flex items-center gap-4">
          <button
            onClick={onBack}
            className="flex items-center justify-center w-10 h-10 rounded-lg bg-vault-700 hover:bg-vault-600 text-slate-300 hover:text-white transition-colors"
            aria-label="Go back"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div>
            <h2 className="text-2xl font-bold text-white mb-1">Settings</h2>
            <p className="text-sm text-slate-400">
              Configure your STL Vault preferences
            </p>
          </div>
        </div>
      </div>

      {/* Content Section */}
      <div className="flex-1 bg-vault-900/30 rounded-lg p-6 text-slate-300">
        <WatcherInbox />

        {/* Slicer Settings */}
        <div className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <Wrench className="w-5 h-5 text-blue-400" />
            <h3 className="text-lg font-semibold text-white">Default Slicer</h3>
          </div>
          <p className="text-sm text-slate-400 mb-4">
            Choose which slicer application to open when clicking "Open in
            Slicer" button
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {ALL_SLICER_TYPES.map((slicer) => (
              <button
                key={slicer}
                onClick={() => handleSlicerChange(slicer)}
                className={`p-4 rounded-lg border-2 transition-all text-left ${
                  launchSlicers.includes(slicer)
                    ? "border-blue-500 bg-blue-500/10 shadow-lg shadow-blue-500/20"
                    : "border-vault-700 bg-vault-800 hover:border-vault-600"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-white">
                    {SLICERS[slicer].name}
                  </span>
                  {launchSlicers.includes(slicer) && (
                    <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                  )}
                </div>
                <span className="text-xs text-slate-500 mt-1 block">
                  {SLICERS[slicer].protocol}
                </span>
              </button>
            ))}
          </div>

          <div className="mt-4 p-4 bg-vault-800 rounded-lg border border-vault-700">
            <p className="text-xs text-slate-400">
              <span className="font-semibold text-slate-300">Note:</span> Your
              slicer application must be installed and configured to handle
              protocol links (e.g., {SLICERS[selectedSlicer].protocol}). The
              exact setup varies by slicer and operating system.
            </p>
          </div>
        </div>

        {/* Import Sources */}
        <div className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <Globe className="w-5 h-5 text-blue-400" />
            <h3 className="text-lg font-semibold text-white">
              Import Sources
            </h3>
          </div>
          <p className="text-sm text-slate-400 mb-4">
            Paste a project URL from any of these into "Import URL" to
            download and group its files automatically.
          </p>

          <div className="space-y-3">
            <div className="p-4 bg-vault-800 rounded-lg border border-vault-700 flex items-center justify-between">
              <span className="text-sm font-medium text-slate-200">
                Printables
              </span>
              <span className="text-xs text-green-400">No setup needed</span>
            </div>

            <div className="p-4 bg-vault-800 rounded-lg border border-vault-700">
              <div className="flex items-center gap-2 mb-1">
                <KeyRound className="w-4 h-4 text-blue-400" />
                <span className="text-sm font-medium text-slate-200">
                  MakerWorld
                </span>
              </div>
              <p className="text-sm text-slate-400 mb-3">
                Add a Bambu Cloud token to enable MakerWorld 3MF downloads.
              </p>
              <form onSubmit={handleMakerWorldTokenSubmit}>
                <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-3">
                  <input
                    type="password"
                    className="w-full bg-vault-900 border border-vault-700 rounded-md px-3 py-2 text-white focus:border-indigo-500 outline-none placeholder:text-slate-600"
                    placeholder={
                      makerWorldTokenConfigured
                        ? "Token configured; paste a new token to replace it"
                        : "Paste MakerWorld token"
                    }
                    value={makerWorldToken}
                    onChange={(e) => {
                      setMakerWorldToken(e.target.value);
                      setMakerWorldTokenStatus("idle");
                    }}
                  />
                  <button
                    type="submit"
                    disabled={!makerWorldToken.trim()}
                    className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Save
                  </button>
                </div>
              </form>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <span
                  className={`text-xs ${
                    makerWorldTokenConfigured
                      ? "text-green-400"
                      : "text-amber-400"
                  }`}
                >
                  {makerWorldTokenConfigured
                    ? "Token configured"
                    : "Token not configured"}
                </span>
                {makerWorldTokenConfigured && (
                  <button
                    type="button"
                    onClick={handleMakerWorldTokenClear}
                    className="text-xs text-slate-400 hover:text-white underline"
                  >
                    Clear token
                  </button>
                )}
                {makerWorldTokenStatus === "saved" && (
                  <span className="text-xs text-green-400">Saved</span>
                )}
                {makerWorldTokenStatus === "cleared" && (
                  <span className="text-xs text-amber-400">Cleared</span>
                )}
                {makerWorldTokenStatus === "error" && (
                  <span className="text-xs text-red-400">Update failed</span>
                )}
                <button
                  type="button"
                  onClick={() => setShowMakerWorldTokenHelp((v) => !v)}
                  className="text-xs text-indigo-400 hover:text-indigo-300 underline ml-auto"
                >
                  {showMakerWorldTokenHelp ? "Hide" : "How do I get this?"}
                </button>
              </div>
              {showMakerWorldTokenHelp && (
                <div className="mt-3 p-3 rounded-lg bg-vault-900 border border-vault-700 text-xs text-slate-400 space-y-2">
                  <ol className="list-decimal list-inside space-y-1">
                    <li>
                      Open{" "}
                      <a
                        href="https://makerworld.com/en"
                        target="_blank"
                        rel="noreferrer"
                        className="text-indigo-400 hover:text-indigo-300 underline"
                      >
                        makerworld.com
                      </a>{" "}
                      and sign in.
                    </li>
                    <li>Press F12 to open Developer Tools.</li>
                    <li>
                      Go to Application → Cookies (Chrome/Edge) or Storage →
                      Cookies (Firefox), then select the makerworld.com
                      domain.
                    </li>
                    <li>Find the row named "token" and copy its value.</li>
                    <li>Paste it into the field above and click Save.</li>
                  </ol>
                  <p className="text-amber-400">
                    Treat this token like a password — it grants full access
                    to your Bambu account, not just downloads. It also
                    expires roughly every 3 months and will need to be
                    copied again the same way.
                  </p>
                </div>
              )}
            </div>

            <div className="p-4 bg-vault-800 rounded-lg border border-vault-700 flex items-center justify-between">
              <span className="text-sm font-medium text-slate-200">
                Other sites (Thingiverse, etc.)
              </span>
              <span className="text-xs text-green-400">No setup needed</span>
            </div>
          </div>
        </div>

        {/* AI Provider (OpenRouter) Settings */}
        <div className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <Sparkles className="w-5 h-5 text-blue-400" />
            <h3 className="text-lg font-semibold text-white">
              AI Provider (OpenRouter)
            </h3>
          </div>
          <p className="text-sm text-slate-400 mb-4">
            Add your own OpenRouter API key to enable AI tag suggestions and
            Etsy pricing estimates on the model detail panel. Your key is
            stored locally on your own server, never sent anywhere but
            OpenRouter.
          </p>
          <form onSubmit={handleOpenRouterKeySubmit}>
            <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-3">
              <input
                type="password"
                className="w-full bg-vault-900 border border-vault-700 rounded-md px-3 py-2 text-white focus:border-indigo-500 outline-none placeholder:text-slate-600"
                placeholder={
                  openRouterKeyConfigured
                    ? "Key configured; paste a new key to replace it"
                    : "Paste OpenRouter API key (sk-or-...)"
                }
                value={openRouterKey}
                onChange={(e) => {
                  setOpenRouterKey(e.target.value);
                  setOpenRouterKeyStatus("idle");
                }}
              />
              <button
                type="submit"
                disabled={!openRouterKey.trim()}
                className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Save
              </button>
            </div>
          </form>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <span
              className={`text-xs ${
                openRouterKeyConfigured ? "text-green-400" : "text-amber-400"
              }`}
            >
              {openRouterKeyConfigured
                ? "Key configured"
                : "Key not configured"}
            </span>
            {openRouterKeyConfigured && (
              <button
                type="button"
                onClick={handleOpenRouterKeyClear}
                className="text-xs text-slate-400 hover:text-white underline"
              >
                Clear key
              </button>
            )}
            {openRouterKeyStatus === "saved" && (
              <span className="text-xs text-green-400">Saved</span>
            )}
            {openRouterKeyStatus === "cleared" && (
              <span className="text-xs text-amber-400">Cleared</span>
            )}
            {openRouterKeyStatus === "error" && (
              <span className="text-xs text-red-400">Update failed</span>
            )}
          </div>
        </div>

        {/* Api Settings*/}
        <div className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <EthernetPort className="w-5 h-5 text-blue-400" />
            <h3 className="text-lg font-semibold text-white">API Host</h3>
          </div>
          <p className="text-sm text-slate-400 mb-4">Choose the API Host URL</p>
          <div className="mt-4 p-4 bg-vault-800 rounded-lg border border-vault-700 mb-4 ">
            <p className="text-xs text-slate-400">
              <span className="font-semibold text-slate-300">Note:</span> The
              URL set here will override the one in the ENV variables.
            </p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <form onSubmit={handleApiForm}>
              <div className="grid grid-cols-2 mb-4">
                <label className="block col-span-2 text-sm font-medium text-slate-400 mb-1">
                  API URL
                </label>
                <input
                  autoFocus
                  type="string"
                  required
                  className="col-span-2 w-full bg-vault-900 border border-vault-700 rounded-md px-3 py-2 text-white focus:border-indigo-500 outline-none placeholder:text-slate-600"
                  placeholder="http://0.0.0.0:8989"
                  value={selectedApiPort}
                  onChange={(e) => handleApiPortChange(e.target.value)}
                />

                <p className="col-span-2 w-full text-xs text-slate-500 mt-1">
                  Insert the port at which the API is served.
                </p>
              </div>

              <div className="flex gap-3">
                <button
                  type="submit"
                  disabled={!selectedApiPort}
                  className="flex-1 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Set
                </button>
                {apiPortStatus ? (
                  <Check className="flex text-green-400 rounded-full bg-vault-800 my-auto"></Check>
                ) : (
                  <X className="flex text-red-400 rounded-full bg-vault-800 my-auto"></X>
                )}
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Settings;
