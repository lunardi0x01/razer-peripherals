import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui
import "razer_api.js" as RazerApi

Panel {
  id: root
  moduleName: "lunardi0x01.razer-peripherals"
  ipcTarget: "lunardi0x01.razer-peripherals"

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  property var devices: []
  property bool loading: false
  property bool lastFetchFailed: false
  // pid of the device whose colour-apply Process is currently in flight --
  // VARSTORE writes go to flash, so only one at a time per device and the
  // Apply button for it is disabled while this is set, rather than queuing/
  // coalescing repeated writes the way Hue's slider does for live state.
  property string applyingPid: ""
  property var pendingHexByPid: ({})

  readonly property real lowestPercent: {
    var lowest = null
    for (var i = 0; i < root.devices.length; i++) {
      var p = root.devices[i].percent
      if (p === null || p === undefined) continue
      if (lowest === null || p < lowest) lowest = p
    }
    return lowest
  }

  readonly property string statusText: {
    if (root.lastFetchFailed) return "Couldn't read devices"
    if (root.loading && root.devices.length === 0) return "Reading…"
    return ""
  }

  function hexFor(pid) {
    if (Object.prototype.hasOwnProperty.call(root.pendingHexByPid, pid)) {
      return root.pendingHexByPid[pid]
    }
    for (var i = 0; i < root.devices.length; i++) {
      if (root.devices[i].pid === pid) return root.devices[i].lastColor || ""
    }
    return ""
  }

  function setHexFor(pid, hex) {
    var next = {}
    for (var k in root.pendingHexByPid) next[k] = root.pendingHexByPid[k]
    next[pid] = hex
    root.pendingHexByPid = next
  }

  function refresh() {
    if (statusProc.running) return
    root.loading = true
    statusProc.command = RazerApi.apiCmd(["get-status"])
    statusProc.running = true
  }

  function applyColor(pid, hex) {
    if (!RazerApi.isValidPid(pid) || !RazerApi.isValidHexColor(hex)) return
    if (root.applyingPid !== "") return
    root.applyingPid = pid
    applyProc.forPid = pid
    applyProc.command = RazerApi.apiCmd(["set-color", pid, hex])
    applyProc.running = true
  }

  onOpenedChanged: if (opened) root.refresh()

  Timer {
    // Battery drains slowly; there's no reason to poll hidraw as fast as
    // Hue polls a bridge. Only runs while the panel is open.
    interval: 20000
    repeat: true
    running: root.opened
    onTriggered: root.refresh()
  }

  Process {
    id: statusProc
    stdout: StdioCollector {
      id: statusCollector
      waitForEnd: true
    }
    onExited: function(exitCode) {
      root.loading = false
      if (exitCode !== 0) {
        root.lastFetchFailed = true
        return
      }
      root.lastFetchFailed = false
      root.devices = RazerApi.parseStatus(statusCollector.text)
    }
  }

  Process {
    id: applyProc
    property string forPid: ""
    onExited: function(exitCode) {
      if (applyProc.forPid === root.applyingPid) root.applyingPid = ""
      if (exitCode === 0) {
        var next = {}
        for (var k in root.pendingHexByPid) {
          if (k !== applyProc.forPid) next[k] = root.pendingHexByPid[k]
        }
        root.pendingHexByPid = next
        root.refresh()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(320))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Flickable {
        id: scroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: column
          width: scroll.width
          spacing: Style.space(10)

          Row {
            width: parent.width
            spacing: Style.space(10)

            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: "" // nf-fa-keyboard -- verify against the installed
                              // Nerd Font build once loaded in a live shell
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.title
            }

            Column {
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Text {
                text: "Razer Peripherals"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
              }

              Text {
                visible: root.statusText.length > 0
                text: root.statusText
                textFormat: Text.PlainText
                color: Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
          }

          PanelSeparator {
            foreground: root.bar.foreground
          }

          Text {
            visible: root.devices.length === 0 && !root.loading
            width: parent.width
            text: "No responsive Razer devices found. If one is wireless, wake it first (press a key / move it)."
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: Qt.darker(root.bar.foreground, 1.4)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.body
          }

          Repeater {
            model: root.devices

            delegate: Column {
              required property var modelData
              width: column.width
              spacing: Style.space(6)

              Row {
                width: parent.width
                spacing: Style.space(8)

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: modelData.name
                  textFormat: Text.PlainText
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  visible: !modelData.responsive
                  text: "(asleep — last known)"
                  textFormat: Text.PlainText
                  color: Qt.darker(root.bar.foreground, 1.6)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              Row {
                width: parent.width
                spacing: Style.space(8)

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  text: RazerApi.formatPercent(modelData.percent)
                  textFormat: Text.PlainText
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.title
                }

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  visible: modelData.charging
                  text: "charging"
                  textFormat: Text.PlainText
                  color: Qt.darker(root.bar.foreground, 1.4)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              Row {
                width: parent.width
                spacing: Style.space(8)

                Rectangle {
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(22)
                  height: Style.space(22)
                  radius: Style.space(4)
                  border.width: 1
                  border.color: Qt.darker(root.bar.foreground, 1.6)
                  color: {
                    var hex = root.hexFor(modelData.pid)
                    return RazerApi.isValidHexColor(hex) ? ("#" + hex) : "transparent"
                  }
                }

                TextField {
                  id: hexField
                  anchors.verticalCenter: parent.verticalCenter
                  width: Style.space(110)
                  foreground: root.bar.foreground
                  placeholderText: "RRGGBB"
                  text: root.hexFor(modelData.pid)
                  onTextEdited: root.setHexFor(modelData.pid, text)
                }

                Button {
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.applyingPid === modelData.pid ? "Applying…" : "Apply"
                  bordered: true
                  foreground: root.bar.foreground
                  enabled: root.applyingPid === "" && RazerApi.isValidHexColor(hexField.text)
                  opacity: enabled ? 1 : 0.5
                  tooltipText: "Writes this colour to the device's own memory (VARSTORE) — persists across sleep and reboot with no software running."
                  onClicked: root.applyColor(modelData.pid, hexField.text)
                }
              }
            }
          }

          Text {
            width: parent.width
            text: "Colour changes are written to the device's on-board flash and survive sleep/reboot. Apply sparingly — this is a flash write, not a live preview."
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            color: Qt.darker(root.bar.foreground, 1.6)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
