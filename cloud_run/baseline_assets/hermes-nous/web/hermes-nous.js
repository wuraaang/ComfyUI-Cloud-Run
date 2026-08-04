import { app } from "../../scripts/app.js";


export const SETTINGS = Object.freeze({
    enabled: "HermesNous.Enabled",
    mode: "HermesNous.Mode",
    previousPalette: "HermesNous.PreviousPalette",
});

export const PALETTE_IDS = Object.freeze({
    light: "hermes-nous-light",
    dark: "hermes-nous-dark",
});

export const HERMES_NOUS_PALETTES = Object.freeze({
    [PALETTE_IDS.light]: {
        id: PALETTE_IDS.light,
        name: "Hermes Nous — Light",
        light_theme: true,
        version: 1,
        colors: {
            node_slot: {
                CLIP: "#9E94D5",
                CLIP_VISION: "#9E94D5",
                CLIP_VISION_OUTPUT: "#4C7F8C",
                CONDITIONING: "#C08532",
                CONTROL_NET: "#4C7F8C",
                IMAGE: "#1F8A65",
                LATENT: "#0053FD",
                MASK: "#C08532",
                MODEL: "#0053FD",
                STYLE_MODEL: "#CF806D",
                VAE: "#DB704B",
                NOISE: "#666678",
                GUIDER: "#0053FD",
                SAMPLER: "#9E94D5",
                SIGMAS: "#4C7F8C",
                TAESD: "#1F8A65",
            },
            litegraph_base: {
                BACKGROUND_IMAGE: "",
                CLEAR_BACKGROUND_COLOR: "#F8FAFF",
                NODE_TITLE_COLOR: "#17171A",
                NODE_SELECTED_TITLE_COLOR: "#0053FD",
                NODE_TEXT_COLOR: "#17171A",
                NODE_TEXT_HIGHLIGHT_COLOR: "#0053FD",
                NODE_DEFAULT_COLOR: "#EDF3FF",
                NODE_DEFAULT_BGCOLOR: "#FFFFFF",
                NODE_DEFAULT_BOXCOLOR: "#0053FD",
                NODE_DEFAULT_SHAPE: 2,
                NODE_BOX_OUTLINE_COLOR: "rgba(0, 83, 253, 0.22)",
                NODE_BYPASS_BGCOLOR: "#F2F6FF",
                NODE_ERROR_COLOUR: "#C72E4D",
                DEFAULT_SHADOW_COLOR: "rgba(0, 0, 0, 0.08)",
                WIDGET_BGCOLOR: "#FCFCFC",
                WIDGET_OUTLINE_COLOR: "rgba(0, 83, 253, 0.22)",
                WIDGET_TEXT_COLOR: "#17171A",
                WIDGET_SECONDARY_TEXT_COLOR: "#666678",
                WIDGET_DISABLED_TEXT_COLOR: "rgba(23, 23, 26, 0.36)",
                LINK_COLOR: "#0053FD",
                EVENT_LINK_COLOR: "#CF806D",
                CONNECTING_LINK_COLOR: "#0053FD",
                BADGE_FG_COLOR: "#17171A",
                BADGE_BG_COLOR: "#EDF3FF",
            },
            comfy_base: {
                "fg-color": "#17171A",
                "bg-color": "#F8FAFF",
                "comfy-menu-bg": "#FFFFFF",
                "comfy-menu-secondary-bg": "#F3F7FF",
                "comfy-input-bg": "#FCFCFC",
                "input-text": "#17171A",
                "descrip-text": "#666678",
                "drag-text": "#0053FD",
                "error-text": "#C72E4D",
                "border-color": "rgba(0, 83, 253, 0.22)",
                "tr-even-bg-color": "#FFFFFF",
                "tr-odd-bg-color": "#F8FAFF",
                "content-bg": "#FFFFFF",
                "content-fg": "#17171A",
                "content-hover-bg": "#EDF3FF",
                "content-hover-fg": "#17171A",
                "bar-shadow": "rgba(0, 0, 0, 0.08)",
                "contrast-mix-color": "#17171A",
                "interface-stroke": "rgba(0, 83, 253, 0.22)",
                "interface-panel-surface": "#FFFFFF",
                "interface-panel-box-shadow": "0 0 0 1px rgba(0, 83, 253, 0.10), 0 8px 24px rgba(0, 0, 0, 0.08)",
                "interface-panel-drop-shadow": "rgba(0, 0, 0, 0.08)",
                "interface-panel-hover-surface": "#F2F6FF",
                "interface-panel-selected-surface": "#EDF3FF",
                "interface-button-hover-surface": "#F2F6FF",
            },
        },
    },
    [PALETTE_IDS.dark]: {
        id: PALETTE_IDS.dark,
        name: "Hermes Nous — Dark",
        light_theme: false,
        version: 1,
        colors: {
            node_slot: {
                CLIP: "#9E94D5",
                CLIP_VISION: "#9E94D5",
                CLIP_VISION_OUTPUT: "#6F9BA6",
                CONDITIONING: "#C08532",
                CONTROL_NET: "#6F9BA6",
                IMAGE: "#55A583",
                LATENT: "#B5C7F3",
                MASK: "#C08532",
                MODEL: "#FFE6CB",
                STYLE_MODEL: "#CF806D",
                VAE: "#DB704B",
                NOISE: "#B5C7F3",
                GUIDER: "#FFE6CB",
                SAMPLER: "#9E94D5",
                SIGMAS: "#6F9BA6",
                TAESD: "#55A583",
            },
            litegraph_base: {
                BACKGROUND_IMAGE: "",
                CLEAR_BACKGROUND_COLOR: "#0D2F86",
                NODE_TITLE_COLOR: "#FFE6CB",
                NODE_SELECTED_TITLE_COLOR: "#FFE6CB",
                NODE_TEXT_COLOR: "#FFE6CB",
                NODE_TEXT_HIGHLIGHT_COLOR: "#F0F4FF",
                NODE_DEFAULT_COLOR: "#1540B1",
                NODE_DEFAULT_BGCOLOR: "#12378F",
                NODE_DEFAULT_BOXCOLOR: "#0053FD",
                NODE_DEFAULT_SHAPE: 2,
                NODE_BOX_OUTLINE_COLOR: "#3158AD",
                NODE_BYPASS_BGCOLOR: "#183F9A",
                NODE_ERROR_COLOUR: "#C0473A",
                DEFAULT_SHADOW_COLOR: "rgba(0, 0, 0, 0.28)",
                WIDGET_BGCOLOR: "#0B2566",
                WIDGET_OUTLINE_COLOR: "#3158AD",
                WIDGET_TEXT_COLOR: "#FFE6CB",
                WIDGET_SECONDARY_TEXT_COLOR: "#B5C7F3",
                WIDGET_DISABLED_TEXT_COLOR: "rgba(181, 199, 243, 0.54)",
                LINK_COLOR: "#B5C7F3",
                EVENT_LINK_COLOR: "#DB704B",
                CONNECTING_LINK_COLOR: "#FFE6CB",
                BADGE_FG_COLOR: "#FFE6CB",
                BADGE_BG_COLOR: "#1540B1",
            },
            comfy_base: {
                "fg-color": "#FFE6CB",
                "bg-color": "#0D2F86",
                "comfy-menu-bg": "#123A96",
                "comfy-menu-secondary-bg": "#12378F",
                "comfy-input-bg": "#0B2566",
                "input-text": "#FFE6CB",
                "descrip-text": "#B5C7F3",
                "drag-text": "#FFE6CB",
                "error-text": "#E75E78",
                "border-color": "#3158AD",
                "tr-even-bg-color": "#12378F",
                "tr-odd-bg-color": "#143B91",
                "content-bg": "#12378F",
                "content-fg": "#FFE6CB",
                "content-hover-bg": "#1B45A4",
                "content-hover-fg": "#F0F4FF",
                "bar-shadow": "rgba(0, 0, 0, 0.28)",
                "contrast-mix-color": "#FFE6CB",
                "interface-stroke": "#3158AD",
                "interface-panel-surface": "#12378F",
                "interface-panel-box-shadow": "0 0 0 1px rgba(255, 230, 203, 0.08), 0 8px 24px rgba(0, 0, 0, 0.24)",
                "interface-panel-drop-shadow": "rgba(0, 0, 0, 0.24)",
                "interface-panel-hover-surface": "#183F9A",
                "interface-panel-selected-surface": "#1B45A4",
                "interface-button-hover-surface": "#1B45A4",
            },
        },
    },
});

const CORE_PALETTE_IDS = new Set(["dark", "light", "solarized", "arc", "nord", "github"]);
const HERMES_PALETTE_IDS = new Set(Object.values(PALETTE_IDS));

function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function mergeHermesPalettes(existing) {
    const current = isRecord(existing) ? existing : {};
    const merged = { ...current };
    let changed = !isRecord(existing);

    for (const [id, palette] of Object.entries(HERMES_NOUS_PALETTES)) {
        if (JSON.stringify(current[id]) !== JSON.stringify(palette)) {
            merged[id] = palette;
            changed = true;
        }
    }

    return { palettes: changed ? merged : current, changed };
}

export function resolveThemeMode(mode, systemDark) {
    if (mode === "Light") return "light";
    if (mode === "System") return systemDark ? "dark" : "light";
    return "dark";
}

export function createThemeController({ app: appRef, document: documentRef, window: windowRef, schedule }) {
    const cachedSettings = new Map([
        [SETTINGS.enabled, false],
        [SETTINGS.mode, "Dark"],
        [SETTINGS.previousPalette, ""],
    ]);
    const scheduleCallback = schedule ?? ((callback) => {
        if (typeof windowRef?.requestAnimationFrame === "function") {
            windowRef.requestAnimationFrame(callback);
        } else {
            queueMicrotask(callback);
        }
    });
    let installed = false;
    let live = false;
    let mediaQuery = null;
    let warned = false;
    let syncQueue = Promise.resolve();

    function warnOnce(error) {
        if (warned) return;
        warned = true;
        console.warn("[Hermes Nous] Palette API unavailable; applying scoped shell CSS only.", error ?? "");
    }

    function settingsApi() {
        return appRef?.ui?.settings;
    }

    function hasPaletteApi() {
        const api = settingsApi();
        return typeof api?.getSettingValue === "function"
            && (typeof api?.setSettingValueAsync === "function" || typeof api?.setSettingValue === "function");
    }

    function readSetting(id) {
        const api = settingsApi();
        if (typeof api?.getSettingValue === "function") {
            const value = api.getSettingValue(id);
            if (value !== undefined) return value;
        }
        return cachedSettings.get(id);
    }

    async function writeSetting(id, value) {
        cachedSettings.set(id, value);
        const api = settingsApi();
        if (typeof api?.setSettingValueAsync === "function") {
            await api.setSettingValueAsync(id, value);
            return true;
        }
        if (typeof api?.setSettingValue === "function") {
            api.setSettingValue(id, value);
            return true;
        }
        return false;
    }

    function ensureStylesheet() {
        if (documentRef.querySelector("link[data-hermes-nous-style]")) return;
        const link = documentRef.createElement("link");
        link.rel = "stylesheet";
        link.href = new URL("./hermes-nous.css", import.meta.url).href;
        link.dataset.hermesNousStyle = "";
        documentRef.head.append(link);
    }

    function requestRepaint() {
        if (typeof appRef?.canvas?.setDirty === "function") {
            appRef.canvas.setDirty(true, true);
        }
    }

    function customPalettes() {
        const value = readSetting("Comfy.CustomColorPalettes");
        return isRecord(value) ? value : {};
    }

    function paletteExists(id) {
        return CORE_PALETTE_IDS.has(id) || Object.hasOwn(customPalettes(), id);
    }

    function resolvedMode() {
        if (!mediaQuery && typeof windowRef?.matchMedia === "function") {
            mediaQuery = windowRef.matchMedia("(prefers-color-scheme: dark)");
        }
        return resolveThemeMode(readSetting(SETTINGS.mode), mediaQuery?.matches ?? true);
    }

    async function ensurePalettes() {
        if (!hasPaletteApi()) {
            warnOnce();
            return;
        }
        const result = mergeHermesPalettes(readSetting("Comfy.CustomColorPalettes"));
        if (result.changed) {
            await writeSetting("Comfy.CustomColorPalettes", result.palettes);
        }
    }

    async function sync() {
        const enabled = readSetting(SETTINGS.enabled) === true;
        const root = documentRef.documentElement;

        if (!enabled) {
            root.removeAttribute("data-hermes-nous");
            if (!hasPaletteApi()) {
                warnOnce();
                requestRepaint();
                return;
            }

            const current = readSetting("Comfy.ColorPalette");
            const previous = readSetting(SETTINGS.previousPalette);
            if (HERMES_PALETTE_IDS.has(current)) {
                const restored = previous && paletteExists(previous) ? previous : "dark";
                await writeSetting("Comfy.ColorPalette", restored);
            }
            if (previous) {
                await writeSetting(SETTINGS.previousPalette, "");
            }
            requestRepaint();
            return;
        }

        const mode = resolvedMode();
        const paletteId = PALETTE_IDS[mode];
        root.setAttribute("data-hermes-nous", mode);

        if (!hasPaletteApi()) {
            warnOnce();
            requestRepaint();
            return;
        }

        const current = readSetting("Comfy.ColorPalette");
        const previous = readSetting(SETTINGS.previousPalette);
        if (!HERMES_PALETTE_IDS.has(current) && !previous && current) {
            await writeSetting(SETTINGS.previousPalette, current);
        }
        if (current !== paletteId) {
            await writeSetting("Comfy.ColorPalette", paletteId);
        }
        requestRepaint();
    }

    function requestSync() {
        if (!live) return syncQueue;
        syncQueue = syncQueue.then(sync).catch(warnOnce);
        return syncQueue;
    }

    function onSettingChange(id, value) {
        cachedSettings.set(id, value);
        void requestSync();
    }

    async function install() {
        if (installed) return;
        installed = true;
        ensureStylesheet();
        await ensurePalettes();

        if (typeof windowRef?.matchMedia === "function") {
            mediaQuery = windowRef.matchMedia("(prefers-color-scheme: dark)");
            const onSystemModeChange = () => {
                if (readSetting(SETTINGS.mode) === "System") void requestSync();
            };
            if (typeof mediaQuery.addEventListener === "function") {
                mediaQuery.addEventListener("change", onSystemModeChange);
            } else if (typeof mediaQuery.addListener === "function") {
                mediaQuery.addListener(onSystemModeChange);
            }
        }

        scheduleCallback(() => {
            live = true;
            void requestSync();
        });
    }

    return { install, onSettingChange, sync };
}

const controller = createThemeController({ app, document, window });

app.registerExtension({
    name: "HermesNous.Theme",
    settings: [
        {
            id: SETTINGS.enabled,
            category: ["Hermes Nous", "Appearance", "Enabled"],
            name: "Hermes Nous: Enabled",
            type: "boolean",
            defaultValue: false,
            onChange: (value) => controller.onSettingChange(SETTINGS.enabled, value),
        },
        {
            id: SETTINGS.mode,
            category: ["Hermes Nous", "Appearance", "Mode"],
            name: "Hermes Nous: Mode",
            type: "combo",
            defaultValue: "Dark",
            options: ["Dark", "Light", "System"],
            onChange: (value) => controller.onSettingChange(SETTINGS.mode, value),
        },
        {
            id: SETTINGS.previousPalette,
            name: "Hermes Nous previous palette",
            type: "hidden",
            defaultValue: "",
        },
    ],
    async setup() {
        await controller.install();
    },
});
