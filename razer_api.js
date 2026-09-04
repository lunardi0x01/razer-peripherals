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
    out.push({
      pid: String(d.pid).toUpperCase(),
      name: sanitizeName(d.name || ("1532:" + d.pid)),
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
    MAX_JSON_TEXT_LENGTH: MAX_JSON_TEXT_LENGTH
  }
}
