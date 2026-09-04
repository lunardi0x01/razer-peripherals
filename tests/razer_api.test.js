const test = require("node:test")
const assert = require("node:assert/strict")
const path = require("node:path")
const RazerApi = require(path.join(__dirname, "..", "razer_api.js"))

test("apiCmd builds a python3 argv array", () => {
  const cmd = RazerApi.apiCmd(["get-status"])
  assert.equal(cmd[0], "python3")
  assert.ok(cmd[1].endsWith("razer_api.py"))
  assert.deepEqual(cmd.slice(2), ["get-status"])
})

test("apiCmd stringifies every argument", () => {
  const cmd = RazerApi.apiCmd(["set-color", "E8", 123])
  assert.deepEqual(cmd.slice(2), ["set-color", "E8", "123"])
})

test("isValidPid accepts 1-4 hex digits", () => {
  assert.equal(RazerApi.isValidPid("E8"), true)
  assert.equal(RazerApi.isValidPid("0258"), true)
  assert.equal(RazerApi.isValidPid("1"), true)
})

test("isValidPid rejects non-hex or overlong values", () => {
  assert.equal(RazerApi.isValidPid("zz"), false)
  assert.equal(RazerApi.isValidPid("12345"), false)
  assert.equal(RazerApi.isValidPid(""), false)
  assert.equal(RazerApi.isValidPid("E8; rm -rf"), false)
})

test("isValidHexColor requires exactly 6 hex digits", () => {
  assert.equal(RazerApi.isValidHexColor("0022AA"), true)
  assert.equal(RazerApi.isValidHexColor("0022aa"), true)
  assert.equal(RazerApi.isValidHexColor("#0022AA"), false)
  assert.equal(RazerApi.isValidHexColor("0022A"), false)
  assert.equal(RazerApi.isValidHexColor("0022AAFF"), false)
  assert.equal(RazerApi.isValidHexColor("GGGGGG"), false)
})

test("sanitizeName strips angle brackets and caps length", () => {
  assert.equal(RazerApi.sanitizeName("<b>Naga</b>"), "bNaga/b")
  assert.equal(RazerApi.sanitizeName("x".repeat(300)).length, 200)
})

test("parseJson rejects arrays, non-objects, and oversized text", () => {
  assert.equal(RazerApi.parseJson("[1,2,3]"), null)
  assert.equal(RazerApi.parseJson("\"just a string\""), null)
  assert.equal(RazerApi.parseJson(""), null)
  assert.equal(RazerApi.parseJson("x".repeat(RazerApi.MAX_JSON_TEXT_LENGTH + 1)), null)
})

test("parseJson accepts a plain object", () => {
  assert.deepEqual(RazerApi.parseJson('{"a":1}'), { a: 1 })
})

test("parseStatus returns [] for malformed or missing devices", () => {
  assert.deepEqual(RazerApi.parseStatus("not json"), [])
  assert.deepEqual(RazerApi.parseStatus("{}"), [])
  assert.deepEqual(RazerApi.parseStatus('{"devices":"nope"}'), [])
})

test("parseStatus parses a well-formed device list", () => {
  const text = JSON.stringify({
    devices: [
      { pid: "e8", name: "Naga V3 Pro (dongle)", percent: 87.12, charging: true,
        lastColor: "0022aa", responsive: true },
    ],
  })
  const devices = RazerApi.parseStatus(text)
  assert.equal(devices.length, 1)
  assert.equal(devices[0].pid, "E8")
  assert.equal(devices[0].percent, 87.12)
  assert.equal(devices[0].charging, true)
  assert.equal(devices[0].lastColor, "0022AA")
  assert.equal(devices[0].responsive, true)
})

test("parseStatus drops entries with an invalid pid", () => {
  const text = JSON.stringify({ devices: [{ pid: "not-hex", name: "x", percent: 50 }] })
  assert.deepEqual(RazerApi.parseStatus(text), [])
})

test("parseStatus clamps out-of-range percent and treats non-numbers as null", () => {
  const text = JSON.stringify({
    devices: [
      { pid: "E7", name: "a", percent: 150 },
      { pid: "E8", name: "b", percent: -10 },
      { pid: "B4", name: "c", percent: "full" },
    ],
  })
  const devices = RazerApi.parseStatus(text)
  assert.equal(devices[0].percent, 100)
  assert.equal(devices[1].percent, 0)
  assert.equal(devices[2].percent, null)
})

test("parseStatus drops an invalid lastColor rather than passing it through", () => {
  const text = JSON.stringify({ devices: [{ pid: "E8", name: "x", lastColor: "javascript:alert(1)" }] })
  assert.equal(RazerApi.parseStatus(text)[0].lastColor, "")
})

test("parseStatus caps the number of devices parsed", () => {
  const devices = []
  for (let i = 0; i < RazerApi.MAX_JSON_TEXT_LENGTH && devices.length < 32; i++) {
    devices.push({ pid: String(i % 16).padStart(1, "0"), name: "x", percent: 50 })
  }
  const text = JSON.stringify({ devices })
  assert.ok(RazerApi.parseStatus(text).length <= 16)
})

test("formatPercent renders a rounded percent or an em dash placeholder", () => {
  assert.equal(RazerApi.formatPercent(86.6), "87%")
  assert.equal(RazerApi.formatPercent(null), "—")
  assert.equal(RazerApi.formatPercent(undefined), "—")
})
