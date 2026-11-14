// LifeOS Tasks: open tasks and running missions, for a second monitor.
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root
    property var tasks: []
    property var missions: []
    property bool online: false
    preferredRepresentation: fullRepresentation

    function get(path, done) {
        var x = new XMLHttpRequest();
        x.onreadystatechange = function () {
            if (x.readyState !== XMLHttpRequest.DONE) return;
            root.online = x.status === 200;
            if (x.status === 200) { try { done(JSON.parse(x.responseText)); } catch (e) {} }
        };
        x.open("GET", "http://127.0.0.1:8765" + path);
        x.setRequestHeader("X-LifeOS-Client", "1");
        x.send();
    }
    function refresh() {
        get("/api/missions?status=active&limit=6", function (d) { root.missions = d; });
        get("/api/tasks?status=open", function (d) { root.tasks = d.slice(0, 14); });
    }
    Timer { interval: 15000; running: true; repeat: true; triggeredOnStart: true; onTriggered: root.refresh() }

    fullRepresentation: ColumnLayout {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 16
        Layout.minimumHeight: Kirigami.Units.gridUnit * 12
        spacing: Kirigami.Units.smallSpacing
        Kirigami.Heading { text: "Crew is working on"; level: 3; visible: root.missions.length > 0 }
        Repeater {
            model: root.missions
            delegate: PlasmaComponents.Label {
                Layout.fillWidth: true; elide: Text.ElideRight; color: Kirigami.Theme.positiveTextColor
                text: "▶  " + modelData.goal
            }
        }
        Kirigami.Heading { text: "Tasks"; level: 2 }
        PlasmaComponents.Label { visible: !root.online; opacity: 0.7; text: "LifeOS isn't running yet" }
        PlasmaComponents.Label { visible: root.online && root.tasks.length === 0; opacity: 0.7; text: "Nothing to do. Nice." }
        Repeater {
            model: root.tasks
            delegate: RowLayout {
                Layout.fillWidth: true
                PlasmaComponents.Label { text: "☐" }
                PlasmaComponents.Label { Layout.fillWidth: true; elide: Text.ElideRight; text: modelData.title }
                PlasmaComponents.Label {
                    visible: !!modelData.due_at; opacity: 0.7
                    text: modelData.due_at ? String(modelData.due_at).substring(5, 16).replace("T", " ") : ""
                }
            }
        }
        Item { Layout.fillHeight: true }
    }
}
