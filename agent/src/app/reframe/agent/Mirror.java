package app.reframe.agent;

import android.content.Context;
import android.content.SharedPreferences;
import android.media.AudioManager;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;

/**
 * Keeps a copy of the frame's slideshow on the frame: the page, the playlist and
 * the files, so LocalServer can play it while the server is off. Runs on its own
 * thread, once a minute. Settings changed on the frame apply here at once and go
 * to the server at the start of the next round.
 */
final class Mirror implements Runnable {
    private static final long ROUND_MS = 60 * 1000;
    private static final int HTTP_TIMEOUT_MS = 30 * 1000;

    private final SharedPreferences prefs;
    private final AudioManager audio;
    private final File cache, media;
    private volatile JSONObject slideshow;

    Mirror(Context context) {
        prefs = context.getSharedPreferences("agent", Context.MODE_PRIVATE);
        audio = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        cache = new File(context.getFilesDir(), "slideshow.json");
        media = new File(context.getFilesDir(), "media");
        media.mkdirs();
        try {
            String text = Stats.readFile(cache);
            if (text != null) slideshow = new JSONObject(text);
        } catch (Exception ignored) {
        }
    }

    public void run() {
        while (true) {
            try {
                sync();
            } catch (Exception e) {
                Log.w(AgentService.TAG, "mirror: " + e);
            }
            try {
                Thread.sleep(ROUND_MS);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    boolean ready() {
        return slideshow != null;
    }

    String page() {
        return slideshow.optString("page");
    }

    /** The playlist as the page expects it, with only the files already here. */
    JSONObject playlist() throws Exception {
        JSONObject s = slideshow;
        JSONArray items = s.getJSONArray("items"), here = new JSONArray();
        for (int i = 0; i < items.length(); i++) {
            if (new File(media, items.getJSONObject(i).getString("name")).exists()) here.put(items.get(i));
        }
        return new JSONObject().put("display", s.get("display")).put("items", here).put("agent", true).put("page_version", s.opt("page_version"));
    }

    /** One of the playlist's files, or null. */
    File file(String name) throws Exception {
        JSONArray items = slideshow.getJSONArray("items");
        for (int i = 0; i < items.length(); i++) {
            if (items.getJSONObject(i).getString("name").equals(name)) return new File(media, name);
        }
        return null;
    }

    /** Settings from the frame's panel: shown at once, and sent to the server in the next round. */
    synchronized void change(JSONObject changes) throws Exception {
        JSONObject display = slideshow.getJSONObject("display");
        JSONObject pending = new JSONObject(prefs.getString("pending_display", "{}"));
        for (Iterator<String> keys = changes.keys(); keys.hasNext(); ) {
            String k = keys.next();
            display.put(k, changes.get(k));
            pending.put(k, changes.get(k));
        }
        prefs.edit().putString("pending_display", pending.toString()).apply();
        OutputStream out = new FileOutputStream(cache);
        out.write(slideshow.toString().getBytes("UTF-8"));
        out.close();
        applyVolume(display);
    }

    private void applyVolume(JSONObject display) {
        if (!display.optBoolean("sound")) return;
        int max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC);
        audio.setStreamVolume(AudioManager.STREAM_MUSIC, Math.round(display.optInt("volume") * max / 100f), 0);
    }

    private void sync() throws Exception {
        String server = prefs.getString("server", null), key = prefs.getString("key", null);
        if (server == null || key == null) return;
        String pending = prefs.getString("pending_display", null);
        // Sent before fetching, so the copy below already has them.
        if (pending != null) {
            post(server + "/agent/v1/display", key, pending);
            synchronized (this) {
                if (pending.equals(prefs.getString("pending_display", null))) prefs.edit().remove("pending_display").apply();
            }
        }
        File tmp = new File(cache.getPath() + ".tmp");
        download(server + "/agent/v1/slideshow", key, tmp);
        JSONObject fresh = new JSONObject(Stats.readFile(tmp));
        synchronized (this) {
            if (prefs.contains("pending_display")) return;  // changed meanwhile; next round
            if (!tmp.renameTo(cache)) throw new Exception("couldn't save the slideshow");
            slideshow = fresh;
        }
        applyVolume(fresh.getJSONObject("display"));

        Set<String> names = new HashSet<>();
        JSONArray items = fresh.getJSONArray("items");
        for (int i = 0; i < items.length(); i++) names.add(items.getJSONObject(i).getString("name"));
        for (File f : media.listFiles()) {
            if (!names.contains(f.getName())) f.delete();
        }
        for (String name : names) {
            File f = new File(media, name);
            if (f.exists()) continue;
            File part = new File(media, name + ".part");
            download(server + "/agent/v1/media/" + URLEncoder.encode(name, "UTF-8").replace("+", "%20"), key, part);
            if (!part.renameTo(f)) throw new Exception("couldn't save " + name);
        }
    }

    private static void post(String url, String key, String json) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            c.setConnectTimeout(HTTP_TIMEOUT_MS);
            c.setReadTimeout(HTTP_TIMEOUT_MS);
            c.setRequestProperty("Authorization", "Bearer " + key);
            c.setRequestProperty("Content-Type", "application/json");
            c.setDoOutput(true);
            OutputStream out = c.getOutputStream();
            out.write(json.getBytes("UTF-8"));
            out.close();
            if (c.getResponseCode() != 200) throw new Exception("HTTP " + c.getResponseCode() + " from " + url);
        } finally {
            c.disconnect();
        }
    }

    private static void download(String url, String key, File to) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            c.setConnectTimeout(HTTP_TIMEOUT_MS);
            c.setReadTimeout(HTTP_TIMEOUT_MS);
            c.setRequestProperty("Authorization", "Bearer " + key);
            if (c.getResponseCode() != 200) throw new Exception("HTTP " + c.getResponseCode() + " from " + url);
            InputStream in = c.getInputStream();
            OutputStream out = new FileOutputStream(to);
            try {
                LocalServer.copy(in, out, Long.MAX_VALUE);
            } finally {
                out.close();
                in.close();
            }
        } finally {
            c.disconnect();
        }
    }
}
