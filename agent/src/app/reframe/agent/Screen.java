package app.reframe.agent;

import android.os.PowerManager;
import android.util.Log;

import org.json.JSONObject;

import java.util.Calendar;

/**
 * Decides whether the screen should be on, from the schedule, the room light and
 * touches, and switches it when that decision changes. It acts only on changes, so
 * someone turning the screen on or off by hand isn't overruled until the next one.
 */
final class Screen {
    static final long TOUCH_MS = 5 * 60 * 1000;
    static final long LIGHT_MS = 3 * 1000;  // lit this long before the screen comes back
    static final long OWN_ACTION_MS = 5 * 1000;

    private final PowerManager power;

    float lux = Float.NaN;
    long luxAt;
    private long darkSince, lightSince;
    private long touchUntil;
    private long ownActionUntil;
    private Boolean lastWanted;
    String why = "on";

    Screen(PowerManager power) {
        this.power = power;
    }

    void onLight(float value, long now) {
        lux = value;
        luxAt = now;
    }

    /** The screen came on. If we didn't do it, someone touched it or pressed a button. */
    void onScreenOn(long now) {
        if (now > ownActionUntil) {
            touchUntil = now + TOUCH_MS;
            Log.i(AgentService.TAG, "screen turned on by hand; keeping it on for 5 minutes");
        }
    }

    boolean isOn() {
        return power.isInteractive();
    }

    /** Called about once a second. */
    void tick(JSONObject screen, long now) {
        boolean off = false;
        String reason = "on";

        JSONObject schedule = screen == null ? null : screen.optJSONObject("schedule");
        if (schedule != null && inOffHours(schedule.optString("off"), schedule.optString("on"))) {
            off = true;
            reason = "schedule";
        }

        if (screen != null && screen.optBoolean("dark", false) && !Float.isNaN(lux)) {
            double threshold = screen.optDouble("dark_lux", 5);
            if (lux < threshold) {
                lightSince = 0;
                if (darkSince == 0) darkSince = now;
            } else {
                if (lightSince == 0) lightSince = now;
                if (now - lightSince >= LIGHT_MS) darkSince = 0;
            }
            if (!off && darkSince != 0 && now - darkSince >= screen.optLong("dark_seconds", 30) * 1000) {
                off = true;
                reason = "dark";
            }
        } else {
            darkSince = lightSince = 0;
        }

        boolean wanted = !off;
        if (off && now < touchUntil) {
            wanted = true;
            reason = "touch";
        }
        why = reason;

        if (lastWanted == null || lastWanted != wanted) {
            Log.i(AgentService.TAG, "screen should be " + (wanted ? "on" : "off") + " (" + reason + ")");
            lastWanted = wanted;
            set(wanted, now);
        }
    }

    private void set(boolean on, long now) {
        if (isOn() == on) return;
        ownActionUntil = now + OWN_ACTION_MS;
        // 26 is the power key (a toggle, so only when the state differs); 224 is wake up.
        Root.run(on ? "input keyevent 224" : "input keyevent 26");
    }

    /** Whether now is between off and on (HH:MM, local time), which may span midnight. */
    static boolean inOffHours(String off, String on) {
        int start = minutes(off), end = minutes(on);
        if (start < 0 || end < 0 || start == end) return false;
        Calendar c = Calendar.getInstance();
        int now = c.get(Calendar.HOUR_OF_DAY) * 60 + c.get(Calendar.MINUTE);
        return start < end ? now >= start && now < end : now >= start || now < end;
    }

    private static int minutes(String hhmm) {
        try {
            String[] parts = hhmm.split(":");
            return Integer.parseInt(parts[0]) * 60 + Integer.parseInt(parts[1]);
        } catch (Exception e) {
            return -1;
        }
    }
}
