package app.cozysync.agent;

import android.os.Environment;
import android.os.StatFs;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;

/** The frame's health numbers for a report. */
final class Stats {
    private Stats() {}

    static void fill(JSONObject report, float ambient) throws JSONException {
        JSONObject temps = new JSONObject();
        File[] zones = new File("/sys/class/thermal").listFiles();
        if (zones != null) {
            for (File zone : zones) {
                String type = readFile(new File(zone, "type"));
                String temp = readFile(new File(zone, "temp"));
                if (type == null || temp == null) continue;
                try {
                    double t = Double.parseDouble(temp.trim());
                    temps.put(type.trim(), t > 1000 ? t / 1000 : t);  // some kernels report m°C
                } catch (NumberFormatException ignored) {
                }
            }
        }
        if (!Float.isNaN(ambient)) temps.put("ambient", ambient);
        report.put("temperatures", temps);

        String loadavg = readFile(new File("/proc/loadavg"));
        if (loadavg != null) {
            String[] parts = loadavg.trim().split("\\s+");
            JSONArray load = new JSONArray();
            for (int i = 0; i < 3 && i < parts.length; i++) load.put(Double.parseDouble(parts[i]));
            report.put("load", load);
        }
        report.put("cpus", Runtime.getRuntime().availableProcessors());

        String meminfo = readFile(new File("/proc/meminfo"));
        if (meminfo != null) {
            long total = 0, free = 0, cached = 0, buffers = 0, available = -1;
            for (String line : meminfo.split("\n")) {
                String[] parts = line.split("\\s+");
                if (parts.length < 2) continue;
                long kb = Long.parseLong(parts[1]);
                switch (parts[0]) {
                    case "MemTotal:": total = kb; break;
                    case "MemFree:": free = kb; break;
                    case "Cached:": cached = kb; break;
                    case "Buffers:": buffers = kb; break;
                    case "MemAvailable:": available = kb; break;
                    default: break;
                }
            }
            JSONObject memory = new JSONObject();
            memory.put("total_kb", total);
            // This kernel has no MemAvailable; free plus caches is close.
            memory.put("available_kb", available >= 0 ? available : free + cached + buffers);
            report.put("memory", memory);
        }

        StatFs fs = new StatFs(Environment.getDataDirectory().getPath());
        JSONObject storage = new JSONObject();
        storage.put("total_kb", fs.getTotalBytes() / 1024);
        storage.put("free_kb", fs.getAvailableBytes() / 1024);
        report.put("storage", storage);

        report.put("uptime_seconds", SystemClock.elapsedRealtime() / 1000);
    }

    /** The app on screen, e.g. uk.nktnet.webviewkiosk, or null. */
    static String foreground() {
        // Filtered here rather than with grep: the frame's grep crashes as it exits.
        String out = Root.run("dumpsys activity activities");
        if (out == null) return null;
        for (String line : out.split("\n")) {
            if (!line.contains("mFocusedActivity")) continue;
            for (String word : line.trim().split("\\s+")) {
                int slash = word.indexOf('/');
                if (slash > 0) return word.substring(0, slash);
            }
        }
        return null;
    }

    static String readFile(File file) {
        try {
            return Root.read(new FileInputStream(file));
        } catch (Exception e) {
            return null;
        }
    }
}
