// Same "load under both Quickshell's JS engine and plain Node" trick as
// hue_api.js/ring_api.js: guard the one QML-only call and only assign
// module.exports when module exists.
function resolveScriptPath(fileUrl) {
  return decodeURIComponent(String(fileUrl).replace(/^file:\/\//, ""))
}

var API = typeof Qt !== "undefined"
  ? resolveScriptPath(Qt.resolvedUrl("razer_api.py").toString())
  : "razer_api.py"

function apiCmd(args) {
  var cmd = ["python3", API]
  for (var i = 0; i < args.length; i++) cmd.push(String(args[i]))
  return cmd
}

var MAX_JSON_TEXT_LENGTH = 1024 * 1024
var MAX_DEVICES = 16
var MAX_NAME_LENGTH = 200

var PID_RE = /^[0-9A-Fa-f]{1,4}$/
var HEX_COLOR_RE = /^[0-9A-Fa-f]{6}$/
var VALID_KINDS = ["keyboard", "mouse", "unknown"]

function isValidPid(pid) {
  return PID_RE.test(String(pid))
}

function isValidHexColor(hex) {
  return HEX_COLOR_RE.test(String(hex))
}

// Device names are drawn from a fixed local table in razer_api.py, except
// for the "1532:PID" fallback for an unrecognized device -- neither is
// attacker-influenced the way a Hue bridge's room name is, but strip the
// same markup-ish characters anyway so a hand-edited settings file with
// a stray "<" can't do anything unexpected in a plain Text element.
function sanitizeName(name) {
  return String(name).replace(/[<>]/g, "").slice(0, MAX_NAME_LENGTH)
}

// Nerd Font glyphs verified present in the installed JetBrainsMono Nerd
// Font's cmap (checked directly against the font file's glyph table, the
// same verification method Hue/Ring's own docs call for -- not yet
// confirmed to visually depict the right thing once rendered, only that
// the codepoint isn't missing/tofu). "unknown" falls back to a generic
// desktop-peripheral glyph so an unrecognized device still renders
// something in the panel rather than an empty slot.
var KIND_ICONS = {
  keyboard: "", // nf-fa-keyboard
  mouse: "󰍽",   // nf-md-mouse
  unknown: ""   // nf-fa-computer
}

function deviceIcon(kind) {
  return KIND_ICONS[kind] || KIND_ICONS.unknown
}

// First device of the given kind ("keyboard"/"mouse"), or null. The bar
// widget needs a stable per-slot lookup, not just "whichever device is
// lowest" -- device array order isn't guaranteed to put the keyboard
// before the mouse.
function findByKind(devices, kind) {
  if (!Array.isArray(devices)) return null
  for (var i = 0; i < devices.length; i++) {
    if (devices[i] && devices[i].kind === kind) return devices[i]
  }
  return null
}

function parseJson(text) {
  var raw = String(text || "").trim()
  if (!raw || raw.length > MAX_JSON_TEXT_LENGTH) return null
  try {
    var parsed = JSON.parse(raw)
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null
  } catch (e) {
    return null
  }
}

function parseStatus(text) {
  var obj = parseJson(text)
  if (!obj || !Array.isArray(obj.devices)) return []
  var out = []
  for (var i = 0; i < obj.devices.length && i < MAX_DEVICES; i++) {
    var d = obj.devices[i]
    if (!d || typeof d !== "object") continue
    if (!isValidPid(d.pid)) continue
    var percent = typeof d.percent === "number" && isFinite(d.percent)
      ? Math.max(0, Math.min(100, d.percent)) : null
    var lastColor = isValidHexColor(d.lastColor) ? String(d.lastColor).toUpperCase() : ""
    var kind = VALID_KINDS.indexOf(d.kind) !== -1 ? d.kind : "unknown"
    out.push({
      pid: String(d.pid).toUpperCase(),
      name: sanitizeName(d.name || ("1532:" + d.pid)),
      kind: kind,
      percent: percent,
      charging: !!d.charging,
      lastColor: lastColor,
      responsive: !!d.responsive
    })
  }
  return out
}

function formatPercent(percent) {
  return percent === null || percent === undefined ? "—" : Math.round(percent) + "%"
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    apiCmd: apiCmd,
    resolveScriptPath: resolveScriptPath,
    isValidPid: isValidPid,
    isValidHexColor: isValidHexColor,
    sanitizeName: sanitizeName,
    parseJson: parseJson,
    parseStatus: parseStatus,
    formatPercent: formatPercent,
    deviceIcon: deviceIcon,
    findByKind: findByKind,
    MAX_JSON_TEXT_LENGTH: MAX_JSON_TEXT_LENGTH
  }
}
