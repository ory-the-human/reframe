package app.reframe.agent;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Log;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.DigestInputStream;
import java.security.MessageDigest;

/** Installs the agent APK the server's config names, when it isn't the one running (docs/agent.md). */
final class Updater {
    private static final long RETRY_MS = 60 * 60 * 1000;
    private static final String TMP = "/data/local/tmp/reframe-agent.apk";

    private final Context context;
    private final SharedPreferences prefs;
    private String ownSha;

    Updater(Context context, SharedPreferences prefs) {
        this.context = context;
        this.prefs = prefs;
    }

    /** Why the last update failed, or null. */
    String error() {
        return prefs.getString("update_error", null);
    }

    /** Installs `target` (the config's "agent") if it isn't what's running. On success Android kills us. */
    void check(JSONObject target, String server, String key) throws Exception {
        if (target == null) return;
        String sha = target.getString("sha256");
        if (ownSha == null) ownSha = sha256(new FileInputStream(context.getApplicationInfo().sourceDir));
        if (sha.equals(ownSha)) {
            if (error() != null) prefs.edit().remove("update_error").apply();
            return;
        }
        long now = System.currentTimeMillis();
        if (sha.equals(prefs.getString("update_sha", null)) && Math.abs(now - prefs.getLong("update_at", 0)) < RETRY_MS) return;
        prefs.edit().putString("update_sha", sha).putLong("update_at", now).commit();

        String version = target.optString("version");
        Log.i(AgentService.TAG, "updating to " + version);
        String why;
        try {
            why = install(server + target.getString("url"), key, sha);
        } catch (Exception e) {
            why = e.toString();
        }
        if (why == null) return;
        prefs.edit().putString("update_error", "Couldn't update to " + version + ": " + why).apply();
        Log.w(AgentService.TAG, "update failed: " + why);
    }

    /** Downloads, checks and installs the APK; returns why it failed, or null. */
    private String install(String url, String key, String sha) throws Exception {
        File apk = new File(context.getCacheDir(), "update.apk");
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            c.setConnectTimeout(10 * 1000);
            c.setReadTimeout(30 * 1000);
            c.setRequestProperty("Authorization", "Bearer " + key);
            if (c.getResponseCode() != 200) return "HTTP " + c.getResponseCode() + " from " + url;
            FileOutputStream out = new FileOutputStream(apk);
            DigestInputStream in = new DigestInputStream(c.getInputStream(), MessageDigest.getInstance("SHA-256"));
            byte[] chunk = new byte[8192];
            int n;
            while ((n = in.read(chunk)) > 0) out.write(chunk, 0, n);
            out.close();
            if (!sha.equals(hex(in.getMessageDigest().digest()))) return "the download's sha256 doesn't match";
        } finally {
            c.disconnect();
        }
        // pm (or the system) can't read our cache dir; /data/local/tmp is where adb installs from.
        String out = Root.run("cp " + apk + " " + TMP + " && chmod 644 " + TMP + " && pm install -r -d " + TMP + " 2>&1; rm -f " + TMP);
        apk.delete();
        return out == null ? "couldn't run pm as root" : out.contains("Success") ? null : out.trim();
    }

    private static String sha256(InputStream in) throws Exception {
        MessageDigest d = MessageDigest.getInstance("SHA-256");
        byte[] chunk = new byte[8192];
        int n;
        while ((n = in.read(chunk)) > 0) d.update(chunk, 0, n);
        in.close();
        return hex(d.digest());
    }

    private static String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x));
        return s.toString();
    }
}
