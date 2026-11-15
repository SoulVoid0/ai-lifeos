// LifeOS Clock: a big clock and date, with apartment points and how many agents are working.
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root
    property date now: new Date()
    property int points: -1
    property int working: 0
    preferredRepresentation: fullRepresentation

    function get(path, done) {
        var x = new XMLHttpRequest();
        x.onreadystatechange = function () {
            if (x.readyState === XMLHttpRequest.DONE && x.status === 200) { try { done(JSON.parse(x.responseText)); } catch (e) {} }
        };
        x.open("GET", "http://127.0.0.1:8765" + path);
        x.setRequestHeader("X-LifeOS-Client", "1");
        x.send();
    }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: root.now = new Date() }
    Timer {
        interval: 20000; running: true; repeat: true; triggeredOnStart: true
        onTriggered: {
            root.get("/api/apartment", function (d) { root.points = d.points.balance; });
            root.get("/api/crew/status", function (d) {
                var n = 0;
                for (var i = 0; i < d.agents.length; i++) if (d.agents[i].state === "working") n++;
                root.working = n;
            });
        }
    }

    fullRepresentation: ColumnLayout {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 14
        Layout.minimumHeight: Kirigami.Units.gridUnit * 9
        PlasmaComponents.Label {
            text: Qt.formatTime(root.now, "HH:mm")
            font.pixelSize: Kirigami.Units.gridUnit * 5; font.weight: Font.Light
        }
        PlasmaComponents.Label { text: Qt.formatDate(root.now, "dddd d MMMM yyyy"); font.pixelSize: Kirigami.Units.gridUnit * 1.3 }
        PlasmaComponents.Label {
            opacity: 0.8
            text: (root.points >= 0 ? "★ " + root.points + " points" : "LifeOS isn't running yet")
                  + (root.working > 0 ? "   ·   " + root.working + (root.working === 1 ? " agent" : " agents") + " working" : "")
        }
        Item { Layout.fillHeight: true }
    }
}
