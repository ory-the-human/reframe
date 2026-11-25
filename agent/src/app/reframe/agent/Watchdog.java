package app.reframe.agent;

import android.content.Context;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Keeps the frame the way reframe set it up: the Cozyla app off, the agent and kiosk
 * on the background manager's allow list, the kiosk's settings as the server wrote
 * them, the kiosk in front, and adb answering, and only open just after power-on. Each check runs about every 30 s on
 * the watchdog's own thread; whatever it fixes goes into the next report.
 */
final class Watchdog {
    static final long CHECK_MS = 30 * 1000;
    static final long SETTINGS_GRACE_MS = 2 * 60 * 1000;  // setup rewrites them too
    static final long FRONT_GRACE_MS = 5 * 60 * 1000;     // time for someone using Android's settings
    private static final String KEEP_ALIVE = "kill_background_services_list";
    private static final int ADB_TIMEOUT_MS = 5 * 1000;
    static final long ADB_OPEN_MS = 2 * 60 * 1000;  // after power-on, for recovery

    interface Host {
        JSONObject config();
        boolean screenOn();
        void fixed(String what);
    }

    private final Context context;
    private final Host host;
    private long settingsWrongSince, frontWrongSince;
    private int adbFailures;
    private Set<String> adbWaiting = new HashSet<>();
    /** For the report: "on", "connected" or "off". */
    volatile String adbState;

    Watchdog(Context context, Host host) {
        this.context = context;
        this.host = host;
    }

    void check() {
        JSONObject config = host.config();
        if (config == null) return;
        long now = SystemClock.elapsedRealtime();
        JSONObject kiosk = config.optJSONObject("kiosk");
        keepDisabled(config.optJSONArray("keep_disabled"));
        keepAlive(kiosk == null ? null : kiosk.optString("package", null));
        if (kiosk != null) {
            kioskSettings(kiosk, now);
            kioskInFront(kiosk.optString("package"), now);
        }
        adb();
    }

    private void keepDisabled(JSONArray packages) {
        if (packages == null || packages.length() == 0) return;
        String enabled = Root.run("pm list packages -e");
        if (enabled == null) return;
        for (int i = 0; i < packages.length(); i++) {
            String pkg = packages.optString(i);
            if (!hasPackage(enabled, pkg)) continue;
            if (Root.run("pm disable " + pkg) != null) host.fixed("Turned " + pkg + " off again");
        }
    }

    private void keepAlive(String kioskPkg) {
        String current = Root.run("settings get system " + KEEP_ALIVE);
        if (current == null) return;
        current = current.trim();
        List<String> keep = new ArrayList<>();
        if (!current.isEmpty() && !current.equals("null")) {
            for (String p : current.split(",")) if (!p.isEmpty()) keep.add(p);
        }
        List<String> missing = new ArrayList<>();
        for (String p : new String[] {context.getPackageName(), kioskPkg}) {
            if (p != null && !keep.contains(p)) missing.add(p);
        }
        if (missing.isEmpty()) return;
        keep.addAll(missing);
        if (Root.run("settings put system " + KEEP_ALIVE + " " + join(keep)) != null) {
            host.fixed("Put " + join(missing) + " back on the frame's background allow list");
        }
    }

    /** Compares the kiosk's settings file with the server's values and rewrites the ones that differ. */
    private void kioskSettings(JSONObject kiosk, long now) {
        String pkg = kiosk.optString("package");
        JSONObject wanted = kiosk.optJSONObject("settings");
        if (wanted == null) return;
        String file = "/data/data/" + pkg + "/shared_prefs/user_settings.xml";
        String xml = Root.run("cat " + file);
        if (xml == null || !xml.contains("</map>")) return;

        List<String> wrong = new ArrayList<>();
        for (Iterator<String> keys = wanted.keys(); keys.hasNext(); ) {
            String key = keys.next();
            if (!String.valueOf(wanted.opt(key)).equals(Prefs.get(xml, key))) wrong.add(key);
        }
        if (wrong.isEmpty()) {
            settingsWrongSince = 0;
            return;
        }
        if (settingsWrongSince == 0) settingsWrongSince = now;
        if (now - settingsWrongSince < SETTINGS_GRACE_MS) return;
        settingsWrongSince = 0;

        // Stopped first: the kiosk writes its settings back from memory as it exits.
        Root.run("am force-stop " + pkg);
        String fresh = Root.run("cat " + file);
        if (fresh == null || !fresh.contains("</map>")) return;
        for (String key : wrong) fresh = Prefs.set(fresh, key, wanted.opt(key));
        try {
            int uid = context.getPackageManager().getApplicationInfo(pkg, 0).uid;
            File tmp = new File(context.getFilesDir(), "kiosk-settings.xml");
            OutputStream out = new FileOutputStream(tmp);
            out.write(fresh.getBytes("UTF-8"));
            out.close();
            String done = Root.run("cp " + tmp + " " + file + " && chown " + uid + ":" + uid + " " + file
                    + " && chmod 660 " + file + " && restorecon " + file);
            tmp.delete();
            if (done == null) return;
        } catch (Exception e) {
            Log.w(AgentService.TAG, "couldn't rewrite the kiosk's settings", e);
            return;
        }
        Root.run("am start -a android.intent.action.MAIN -c android.intent.category.HOME");
        host.fixed("Put the kiosk's settings back (" + join(wrong) + ")");
    }

    private void kioskInFront(String pkg, long now) {
        String front = host.screenOn() ? Stats.foreground() : null;
        if (front == null || front.equals(pkg)) {
            frontWrongSince = 0;
            return;
        }
        if (frontWrongSince == 0) frontWrongSince = now;
        if (now - frontWrongSince < FRONT_GRACE_MS) return;
        frontWrongSince = 0;
        if (Root.run("am start -a android.intent.action.MAIN -c android.intent.category.HOME") != null) {
            host.fixed("Brought the slideshow back to the front (" + front + " was there)");
        }
    }

    /**
     * The frames' adbd serves one connection at a time and leaves the others waiting,
     * unanswered. When the one it serves dies without closing (Wi-Fi drops), every new
     * one waits forever: adb says "device offline" until a power cycle. Restarting
     * adbd fixes that. A connection still waiting with the same unread bytes on two
     * checks in a row is stuck; with no connection at all, adbd has to answer one itself.
     */
    private void adb() {
        String port = Root.run("getprop service.adb.tcp.port");
        int p;
        try {
            p = Integer.parseInt(port == null ? "" : port.trim());
        } catch (NumberFormatException e) {
            return;  // adb isn't on the network
        }
        if (p <= 0) {
            adbState = "off";
            return;
        }
        Set<String> waiting = new HashSet<>();
        int open = adbConnections(p, waiting);
        adbState = open > 0 ? "connected" : "on";
        if (open == 0 && SystemClock.elapsedRealtime() >= ADB_OPEN_MS) {
            // Root adb with no password is open to the whole network. init.sun8i.rc
            // opens it again at the next power-on, so power-cycling is the way back in.
            if (Root.run("setprop service.adb.tcp.port -1; stop adbd; start adbd") != null) {
                Log.i(AgentService.TAG, "closed network adb until the next power-on");
                adbState = "off";
            }
            adbWaiting.clear();
            adbFailures = 0;
            return;
        }
        boolean stuck;
        if (open > 0) {
            Set<String> same = new HashSet<>(waiting);
            same.retainAll(adbWaiting);  // waiting just as it was 30 s ago
            stuck = !same.isEmpty();
            adbWaiting = waiting;
        } else {
            adbWaiting.clear();
            // Twice in a row, so a busy moment isn't mistaken for a hang.
            adbFailures = adbAnswers(p) ? 0 : adbFailures + 1;
            stuck = adbFailures >= 2;
        }
        if (!stuck) return;
        adbFailures = 0;
        adbWaiting.clear();
        Root.run("stop adbd; start adbd");
        host.fixed("Restarted adb (it had stopped answering)");
    }

    /** The open connections to adb; adds those with unread bytes to `waiting` (as remote/bytes). */
    static int adbConnections(int port, Set<String> waiting) {
        int open = 0;
        String suffix = String.format(":%04X", port);
        for (String table : new String[] {"/proc/net/tcp", "/proc/net/tcp6"}) {
            String text = Stats.readFile(new File(table));
            if (text == null) continue;
            for (String line : text.split("\n")) {
                String[] f = line.trim().split("\\s+");
                // sl local remote state tx:rx ...; state 01 is ESTABLISHED
                if (f.length < 5 || !f[1].endsWith(suffix) || !f[3].equals("01")) continue;
                open++;
                String[] queues = f[4].split(":");
                if (queues.length == 2 && !queues[1].matches("0+")) waiting.add(f[2] + "/" + queues[1]);
            }
        }
        return open;
    }

    /** Sends adb's opening message (CNXN) and waits for any reply. */
    static boolean adbAnswers(int port) {
        Socket s = new Socket();
        try {
            s.connect(new InetSocketAddress("127.0.0.1", port), ADB_TIMEOUT_MS);
            s.setSoTimeout(ADB_TIMEOUT_MS);
            byte[] payload = "host::\0".getBytes("US-ASCII");
            int sum = 0;
            for (byte b : payload) sum += b & 0xff;
            int cnxn = 0x4e584e43;
            byte[] header = new byte[24];
            putInt(header, 0, cnxn);
            putInt(header, 4, 0x01000000);   // protocol version
            putInt(header, 8, 4096);         // max payload
            putInt(header, 12, payload.length);
            putInt(header, 16, sum);
            putInt(header, 20, ~cnxn);
            OutputStream out = s.getOutputStream();
            out.write(header);
            out.write(payload);
            out.flush();
            InputStream in = s.getInputStream();
            return in.read() >= 0;
        } catch (Exception e) {
            return false;
        } finally {
            try {
                s.close();
            } catch (Exception ignored) {
            }
        }
    }

    private static void putInt(byte[] b, int at, int v) {
        b[at] = (byte) v;
        b[at + 1] = (byte) (v >> 8);
        b[at + 2] = (byte) (v >> 16);
        b[at + 3] = (byte) (v >> 24);
    }

    private static boolean hasPackage(String pmOutput, String pkg) {
        for (String line : pmOutput.split("\n")) if (line.trim().equals("package:" + pkg)) return true;
        return false;
    }

    private static String join(List<String> items) {
        StringBuilder b = new StringBuilder();
        for (String s : items) b.append(b.length() == 0 ? "" : ",").append(s);
        return b.toString();
    }

    /** Reads and sets single values in an Android SharedPreferences file, leaving the rest as it is. */
    static final class Prefs {
        private Prefs() {}

        private static Pattern element(String key) {
            String name = Pattern.quote(escape(key));
            return Pattern.compile("<(string|boolean|int|long|float)\\s+name=\"" + name + "\"(?:\\s+value=\"([^\"]*)\")?\\s*(?:/>|>(.*?)</\\1>)",
                    Pattern.DOTALL);
        }

        /** The value as text ("true", "30", the string itself), or null if it isn't there. */
        static String get(String xml, String key) {
            String value = null;
            Matcher m = element(key).matcher(xml);
            // Android keeps the last one if a key is there twice.
            while (m.find()) {
                value = unescape(m.group(1).equals("string") ? (m.group(3) == null ? "" : m.group(3)) : m.group(2));
            }
            return value;
        }

        /** Sets the key, leaving it there once. */
        static String set(String xml, String key, Object value) {
            String tag;
            if (value instanceof Boolean) {
                tag = "<boolean name=\"" + escape(key) + "\" value=\"" + value + "\" />";
            } else if (value instanceof Integer || value instanceof Long) {
                tag = "<int name=\"" + escape(key) + "\" value=\"" + value + "\" />";
            } else {
                tag = "<string name=\"" + escape(key) + "\">" + escape(String.valueOf(value)) + "</string>";
            }
            xml = element(key).matcher(xml).replaceAll("");
            int end = xml.lastIndexOf("</map>");
            return xml.substring(0, end) + tag + xml.substring(end);
        }

        static String escape(String s) {
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
        }

        static String unescape(String s) {
            return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&apos;", "'")
                    .replace("&#39;", "'").replace("&amp;", "&");
        }
    }
}
