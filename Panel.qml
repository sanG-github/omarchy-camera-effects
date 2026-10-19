import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Bar button and popup for the camera-effects virtual camera. All state lives
// in the daemon: the panel reads `camera-effects status --json` and writes
// through the same CLI, so the terminal and the bar never disagree.
Panel {
  id: root
  moduleName: "sang.camera-effects"
  ipcTarget: "sang.camera-effects"

  readonly property string cli: String(Qt.resolvedUrl("bin/camera-effects")).replace(/^file:\/\//, "")

  property var status: ({ state: "stopped", config: {} })
  readonly property var cfg: status.config || {}
  readonly property string camState: status.state || "stopped"
  readonly property bool serviceOn: camState !== "stopped"
  readonly property bool live: camState === "active"
  // A fresh `omarchy plugin add` has the widget but not the runtime yet.
  readonly property bool needsSetup: status.installed === false
  // The preview is an mpv window on the virtual camera, so it shows up as a user.
  readonly property bool previewing: live && (status.consumers || []).indexOf("mpv") >= 0

  // Keyboard cursor over the rows, in order.
  readonly property var rows: ["service", "preview", "centerStage", "portrait", "studioLight", "mirror", "bgNone", "bgChoose", "bgUpload"]
  property int rowIndex: 0
  property bool cursorActive: false

  readonly property string statusText: {
    if (needsSetup) return "Setup needed"
    if (camState === "active") {
      var who = (status.consumers || []).join(", ")
      return "Live · " + (status.fps || 0) + " fps" + (who ? " · " + who : "")
    }
    if (camState === "idle") return "Ready · camera off until an app uses it"
    if (camState === "error") return "Error · " + (status.error || "")
    return "Off"
  }

  function refresh() {
    if (!statusProc.running) statusProc.running = true
  }

  function run(args) {
    actionProc.command = [root.cli].concat(args)
    actionProc.running = true
  }

  function setLocal(key, value) {
    var next = Object.assign({}, root.status)
    next.config = Object.assign({}, root.cfg)
    next.config[key] = value
    root.status = next
  }

  function toggleEffect(key) {
    setLocal(key, !root.cfg[key])
    run(["toggle", key])
  }

  function activate(row) {
    if (row === "service" && root.needsSetup) {
      Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", root.cli + " setup"])
      root.close()
    }
    else if (row === "service") run([root.serviceOn ? "stop" : "start"])
    else if (row === "preview") { run(["preview"]); root.close() }
    else if (row === "bgNone") { setLocal("backgroundImage", ""); run(["background", "none"]) }
    else if (row === "bgChoose") { run(["background", "choose"]); root.close() }
    else if (row === "bgUpload") { run(["background", "upload"]); root.close() }
    else toggleEffect(row)
  }

  function hover(row) {
    root.cursorActive = true
    root.rowIndex = root.rows.indexOf(row)
  }

  function cursorOn(row) {
    return root.cursorActive && root.rows[root.rowIndex] === row
  }

  onOpenedChanged: if (opened) { cursorActive = false; refresh() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Process {
    id: statusProc
    command: [root.cli, "status", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try { root.status = JSON.parse(text) } catch (e) {}
      }
    }
  }

  Process {
    id: actionProc
    onExited: root.refresh()
  }

  Timer {
    interval: root.opened ? 1000 : 4000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.serviceOn ? "󰖠" : "󱜷"
    active: root.live
    tooltipText: "Camera Effects · " + root.statusText
    onPressed: function(b) {
      if (b === Qt.RightButton) root.run([root.serviceOn ? "stop" : "start"])
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        var d = dy !== 0 ? dy : dx
        root.rowIndex = Math.max(0, Math.min(root.rows.length - 1, root.rowIndex + d))
      }
      onActivateRequested: if (root.cursorActive) root.activate(root.rows[root.rowIndex])
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(12)

        // ---------- Hero ----------
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight)

          Text {
            id: heroIcon
            text: button.text
            color: root.live ? Commons.Color.accent : root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.display
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            Behavior on color { ColorAnimation { duration: 200 } }
          }

          Column {
            id: heroLabels
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(14)
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              text: "Camera Effects"
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
              width: parent.width
            }

            Text {
              text: root.statusText.toUpperCase()
              color: Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              elide: Text.ElideRight
              width: parent.width
            }
          }
        }

        EffectToggle {
          row: "service"
          label: root.needsSetup ? "Set up the virtual camera" : "Virtual camera"
          description: root.needsSetup
            ? "Installs the camera module, Python runtime and models. Asks for your password."
            : "Choose \"FaceTime HD Camera (Effects)\" in your call app."
          checked: root.serviceOn
        }

        Button {
          width: parent.width
          iconText: "󰕧"
          iconSize: Style.font.title
          text: root.previewing ? "Close preview" : "Preview"
          fontSize: Style.font.bodySmall
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
          horizontalPadding: Style.spacing.controlPaddingX
          verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
          bordered: true
          active: root.previewing
          enabled: root.serviceOn
          opacity: enabled ? 1 : 0.5
          hasCursor: root.cursorOn("preview")
          onClicked: root.activate("preview")
          onHovered: function(h) { if (h) root.hover("preview") }
        }

        PanelSeparator { foreground: root.bar.foreground }

        PanelSectionHeader {
          text: "VIDEO EFFECTS"
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
        }

        EffectToggle {
          row: "centerStage"
          label: "Center Stage"
          description: "Pans and zooms to keep you in frame as you move."
          checked: root.cfg.centerStage === true
        }

        StrengthSlider {
          visible: root.cfg.centerStage === true
          key: "centerStageZoom"
          caption: "Zoom"
        }

        EffectToggle {
          row: "portrait"
          label: "Portrait"
          description: "Blurs the background behind you."
          checked: root.cfg.portrait === true
        }

        StrengthSlider {
          visible: root.cfg.portrait === true
          key: "portraitStrength"
        }

        EffectToggle {
          row: "studioLight"
          label: "Studio Light"
          description: "Lights your face and dims the background."
          checked: root.cfg.studioLight === true
        }

        StrengthSlider {
          visible: root.cfg.studioLight === true
          key: "studioLightStrength"
        }

        EffectToggle {
          row: "mirror"
          label: "Mirror video"
          description: "Flip what the call sees. The preview always shows a mirror."
          checked: root.cfg.mirror === true
        }

        PanelSeparator { foreground: root.bar.foreground }

        PanelSectionHeader {
          text: "BACKGROUND"
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
        }

        Row {
          id: bgRow
          width: parent.width
          spacing: Style.space(6)
          readonly property real cellWidth: (width - spacing * 2) / 3

          BgButton {
            row: "bgNone"
            iconText: "󰹑"
            text: "Room"
            active: !root.cfg.backgroundImage
          }

          BgButton {
            row: "bgChoose"
            iconText: "󰸉"
            text: "Choose"
            active: !!root.cfg.backgroundImage
          }

          BgButton {
            row: "bgUpload"
            iconText: "󰕒"
            text: "Upload"
          }
        }

        // The image in use, so "Choose" being lit says which one.
        Item {
          visible: !!root.cfg.backgroundImage
          width: parent.width
          implicitHeight: Math.round(width * 9 / 16)

          Image {
            anchors.fill: parent
            source: root.cfg.backgroundImage ? "file://" + root.cfg.backgroundImage : ""
            fillMode: Image.PreserveAspectCrop
            sourceSize.width: 400
            asynchronous: true
            smooth: true
          }
        }
      }
    }
  }

  component EffectToggle: Toggle {
    property string row: ""
    width: parent.width
    foreground: root.bar.foreground
    accent: Commons.Color.accent
    fontFamily: root.bar.fontFamily
    enabled: row === "service" || root.serviceOn
    opacity: enabled ? 1 : 0.5
    hasCursor: root.cursorOn(row)
    onHovered: function(h) { if (h) root.hover(row) }
    onClicked: root.activate(row)
  }

  component BgButton: Button {
    property string row: ""
    width: bgRow.cellWidth
    iconSize: Style.font.title
    fontSize: Style.font.bodySmall
    foreground: root.bar.foreground
    fontFamily: root.bar.fontFamily
    horizontalPadding: Style.spacing.controlPaddingX
    verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
    bordered: true
    enabled: root.serviceOn
    opacity: enabled ? 1 : 0.5
    hasCursor: root.cursorOn(row)
    onClicked: root.activate(row)
    onHovered: function(h) { if (h) root.hover(row) }
  }

  component StrengthSlider: Item {
    property string key: ""
    property string caption: ""
    width: parent.width
    implicitHeight: slider.implicitHeight + Style.space(4)

    PanelSlider {
      id: slider
      bar: root.bar
      anchors.left: parent.left
      anchors.right: pct.left
      anchors.leftMargin: cap.text !== "" ? cap.implicitWidth + Style.space(14) : Style.space(6)
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      minimum: 0
      maximum: 1
      step: 0.05
      value: root.cfg[parent.key] !== undefined ? root.cfg[parent.key] : 0.5
      onReleased: function(v) {
        root.setLocal(parent.key, v)
        root.run(["set", parent.key, String(v)])
      }
    }

    Text {
      id: cap
      text: parent.caption
      color: Qt.darker(root.bar.foreground, 1.4)
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      anchors.left: parent.left
      anchors.leftMargin: Style.space(6)
      anchors.verticalCenter: parent.verticalCenter
    }

    Text {
      id: pct
      text: Math.round((slider.dragging ? slider.liveValue : slider.value) * 100) + "%"
      color: Qt.darker(root.bar.foreground, 1.4)
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      width: Style.space(36)
      horizontalAlignment: Text.AlignRight
      anchors.right: parent.right
      anchors.rightMargin: Style.space(6)
      anchors.verticalCenter: parent.verticalCenter
    }
  }
}
