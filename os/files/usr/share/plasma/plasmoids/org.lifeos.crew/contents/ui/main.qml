// LifeOS Crew: every agent and what it is doing, for a second monitor.
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root
    property var agents: []
    property bool online: false
    preferredRepresentation: fullRepresentation
    readonly property var colors: ({ violet: "#8b5cf6", amber: "#f59e0b", cyan: "#06b6d4", emerald: "#10b981", rose: "#f43f5e",
        blue: "#3b82f6", orange: "#f97316", pink: "#ec4899", lime: "#84cc16", teal: "#14b8a6", slate: "#64748b", red: "#ef4444",
        green: "#22c55e", yellow: "#eab308", indigo: "#6366f1", purple: "#a855f7", sky: "#0ea5e9", fuchsia: "#d946ef" })

    function refresh() {
        var x = new XMLHttpRequest();
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return;
            root.online = x.status === 200;
            if (x.status === 200) { try { root.agents = JSON.parse(x.responseText).agents; } catch (e) {} }
        };
        x.open("GET", "http://127.0.0.1:8765/api/crew/status");
        x.setRequestHeader("X-LifeOS-Client", "1");
        x.send();
    }
    function doing(a) {
        if (!a.enabled) return "Off duty";
        if (a.state === "working") return "Working: " + (a.current ? a.current.title : "a task");
        if (a.state === "asking") return "Has a question for you";
        if (a.state === "waiting") return "Waiting for your approval";
        var h = new Date().getHours();
        if (h >= 23 || h < 7) return "Sleeping";
        return "Free";
    }
    Timer { interval: 5000; running: true; repeat: true; triggeredOnStart: true; onTriggered: root.refresh() }

    fullRepresentation: ColumnLayout {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 16
        Layout.minimumHeight: Kirigami.Units.gridUnit * 14
        spacing: Kirigami.Units.smallSpacing
        Kirigami.Heading { text: "Crew"; level: 2 }
        PlasmaComponents.Label { visible: !root.online; opacity: 0.7; text: "LifeOS isn't running yet" }
        Repeater {
            model: root.agents
            delegate: RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
                Rectangle {
                    width: Kirigami.Units.gridUnit * 0.7; height: width; radius: width / 2
                    color: root.colors[modelData.color] || Kirigami.Theme.highlightColor
                    opacity: modelData.state === "working" ? 1 : 0.5
                }
                PlasmaComponents.Label { text: modelData.name; font.bold: true }
                PlasmaComponents.Label {
                    Layout.fillWidth: true; elide: Text.ElideRight; opacity: 0.8
                    text: root.doing(modelData)
                    color: modelData.state === "asking" || modelData.state === "waiting" ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.textColor
                }
            }
        }
        Item { Layout.fillHeight: true }
    }
}
