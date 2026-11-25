package app.cozysync.agent;

import android.app.Notification;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.PowerManager;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.TimeZone;

/**
 * The cozysync agent: keeps the frame's screen on its schedule and the room light,
 * keeps the clock right, and reports the frame's health to the cozysync server.
 *
 * It only ever calls the server (docs/agent.md), and keeps working on its last
 * config while the server is unreachable. Started at boot, and by cozysync with the
 * extras "server" (base URL) and "key" (the frame's agent key) to set it up.
 */
public class AgentService extends Service {
    static final String TAG = "cozysync-agent";
    private static final long CLOCK_SLACK_MS = 10 * 1000;
    private static final int HTTP_TIMEOUT_MS = 10 * 1000;

    private HandlerThread thread;
    private Handler handler;
    private PowerManager.WakeLock wakeLock;
    private Screen screen;
    private JSONObject config;
    private boolean refetch = true;
    private long nextReport;
    private float ambient = Float.NaN;
    private JSONArray fixes = new JSONArray();
    private String version = "?";

    @Override
    public void onCreate() {
        super.onCreate();
        Notification note = new Notification.Builder(this)
                .setContentTitle("cozysync agent")
                .setSmallIcon(android.R.drawable.ic_menu_gallery)
                .setPriority(Notification.PRIORITY_MIN)
                .build();
        startForeground(1, note);

        try {
            version = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception ignored) {
        }
        PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
        // The frame is always plugged in; staying awake keeps the light sensor and
        // the schedule running while the screen is off.
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, TAG);
        wakeLock.acquire();
        screen = new Screen(power);
        config = loadConfig();

        thread = new HandlerThread(TAG);
        thread.start();
        handler = new Handler(thread.getLooper());

        SensorManager sensors = (SensorManager) getSystemService(SENSOR_SERVICE);
        listen(sensors, Sensor.TYPE_LIGHT);
        listen(sensors, Sensor.TYPE_AMBIENT_TEMPERATURE);

        registerReceiver(new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                final long now = SystemClock.elapsedRealtime();
                handler.post(new Runnable() {
                    public void run() {
                        screen.onScreenOn(now);
                    }
                });
            }
        }, new IntentFilter(Intent.ACTION_SCREEN_ON), null, handler);

        handler.post(tick);
        Log.i(TAG, "agent " + version + " started");
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && intent.hasExtra("server") && intent.hasExtra("key")) {
            prefs().edit()
                    .putString("server", intent.getStringExtra("server").replaceAll("/+$", ""))
                    .putString("key", intent.getStringExtra("key"))
                    .apply();
            Log.i(TAG, "set up for " + intent.getStringExtra("server"));
            handler.post(new Runnable() {
                public void run() {
                    refetch = true;
                    nextReport = 0;
                }
            });
        }
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        thread.quit();
        wakeLock.release();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void listen(SensorManager sensors, int type) {
        Sensor sensor = sensors.getDefaultSensor(type);
        if (sensor == null) return;
        sensors.registerListener(new SensorEventListener() {
            public void onSensorChanged(SensorEvent event) {
                if (event.sensor.getType() == Sensor.TYPE_LIGHT) {
                    screen.onLight(event.values[0], SystemClock.elapsedRealtime());
                } else {
                    ambient = event.values[0];
                }
            }

            public void onAccuracyChanged(Sensor s, int accuracy) {}
        }, sensor, SensorManager.SENSOR_DELAY_NORMAL, handler);
    }

    private final Runnable tick = new Runnable() {
        public void run() {
            long now = SystemClock.elapsedRealtime();
            try {
                screen.tick(config == null ? null : config.optJSONObject("screen"), now);
                if (now >= nextReport) {
                    nextReport = now + 1000L * (config == null ? 60 : config.optInt("report_seconds", 60));
                    talkToServer();
                }
            } catch (Exception e) {
                Log.e(TAG, "tick failed", e);
            }
            handler.postDelayed(this, 1000);
        }
    };

    private void talkToServer() {
        String server = prefs().getString("server", null);
        String key = prefs().getString("key", null);
        if (server == null || key == null) return;
        try {
            if (refetch || config == null) fetchConfig(server, key);
            // Fixes found while this report is on its way go into the next one.
            JSONArray sending = fixes;
            fixes = new JSONArray();
            JSONObject reply;
            try {
                reply = new JSONObject(call(server + "/agent/v1/report", key, report(sending)));
            } catch (Exception e) {
                for (int i = 0; i < sending.length(); i++) fixes.put(sending.get(i));
                throw e;
            }
            if (!reply.optString("config_version").equals(config.optString("version"))) fetchConfig(server, key);
        } catch (Exception e) {
            Log.w(TAG, "server unreachable: " + e);
        }
    }

    private void fetchConfig(String server, String key) throws Exception {
        refetch = true;
        JSONObject fresh = new JSONObject(call(server + "/agent/v1/config", key, null));
        config = fresh;
        saveConfig(fresh);
        refetch = false;
        Log.i(TAG, "config " + fresh.optString("version"));
    }

    private JSONObject report(JSONArray sending) throws Exception {
        JSONObject r = new JSONObject();
        r.put("agent_version", version);
        r.put("config_version", config == null ? null : config.optString("version"));
        SimpleDateFormat iso = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssZ", Locale.US);
        r.put("time", iso.format(new Date()));
        r.put("timezone", TimeZone.getDefault().getID());
        Stats.fill(r, ambient);
        JSONObject s = new JSONObject();
        s.put("on", screen.isOn());
        s.put("why", screen.why);
        if (!Float.isNaN(screen.lux)) {
            s.put("lux", screen.lux);
            s.put("lux_age_seconds", (SystemClock.elapsedRealtime() - screen.luxAt) / 1000);
        }
        s.put("brightness", Settings.System.getInt(getContentResolver(), Settings.System.SCREEN_BRIGHTNESS, -1));
        r.put("screen", s);
        r.put("foreground", Stats.foreground());
        r.put("fixes", sending);
        return r;
    }

    /** GET (body null) or POST JSON; returns the response body. Also sets the clock from the reply. */
    private String call(String url, String key, JSONObject body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        try {
            c.setConnectTimeout(HTTP_TIMEOUT_MS);
            c.setReadTimeout(HTTP_TIMEOUT_MS);
            c.setRequestProperty("Authorization", "Bearer " + key);
            if (body != null) {
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                OutputStream out = c.getOutputStream();
                out.write(body.toString().getBytes("UTF-8"));
                out.close();
            }
            int status = c.getResponseCode();
            if (status != 200) throw new Exception("HTTP " + status + " from " + url);
            checkClock(c.getDate());
            return Root.read(c.getInputStream());
        } finally {
            c.disconnect();
        }
    }

    /** Nothing else sets the clock on a kiosk frame, and it starts in 1970 after a reboot. */
    private void checkClock(long serverMs) throws Exception {
        if (serverMs <= 0) return;
        long off = System.currentTimeMillis() - serverMs;
        if (Math.abs(off) < CLOCK_SLACK_MS) return;
        SimpleDateFormat set = new SimpleDateFormat("MMddHHmmyyyy.ss", Locale.US);
        set.setTimeZone(TimeZone.getTimeZone("UTC"));
        if (Root.run("date -u " + set.format(new Date(serverMs))) != null) {
            String what = "Set the clock from the server (it was " + describe(off) + ")";
            Log.i(TAG, what);
            JSONObject fix = new JSONObject();
            fix.put("at", serverMs / 1000);
            fix.put("what", what);
            fixes.put(fix);
        }
    }

    private static String describe(long offMs) {
        long s = Math.abs(offMs) / 1000;
        String amount = s < 120 ? s + " s" : s < 7200 ? s / 60 + " min" : s < 172800 ? s / 3600 + " h" : s / 86400 + " days";
        return amount + (offMs > 0 ? " ahead" : " behind");
    }

    private SharedPreferences prefs() {
        return getSharedPreferences("agent", MODE_PRIVATE);
    }

    private File configFile() {
        return new File(getFilesDir(), "config.json");
    }

    private JSONObject loadConfig() {
        String text = Stats.readFile(configFile());
        try {
            return text == null ? null : new JSONObject(text);
        } catch (Exception e) {
            return null;
        }
    }

    private void saveConfig(JSONObject c) throws Exception {
        File tmp = new File(getFilesDir(), "config.json.tmp");
        FileOutputStream out = new FileOutputStream(tmp);
        out.write(c.toString().getBytes("UTF-8"));
        out.close();
        if (!tmp.renameTo(configFile())) throw new Exception("couldn't save the config");
    }
}
