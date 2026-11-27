package app.reframe.agent;

import android.content.Context;
import android.content.pm.PackageManager;
import android.net.wifi.ScanResult;
import android.net.wifi.WifiConfiguration;
import android.net.wifi.WifiInfo;
import android.net.wifi.WifiManager;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** The settings panel's Wi-Fi list: nearby networks, and joining one. Wi-Fi can't be turned off from here. */
final class Wifi {
    private final Context context;
    private final WifiManager wifi;

    Wifi(Context context) {
        this.context = context;
        wifi = (WifiManager) context.getApplicationContext().getSystemService(Context.WIFI_SERVICE);
    }

    /** Networks by signal, strongest first; each call starts a new scan for the next one. */
    JSONObject list() throws Exception {
        String location = "android.permission.ACCESS_COARSE_LOCATION";
        if (context.checkSelfPermission(location) != PackageManager.PERMISSION_GRANTED) {
            Root.run("pm grant " + context.getPackageName() + " " + location);
        }
        wifi.startScan();
        WifiInfo info = wifi.getConnectionInfo();
        String current = info == null ? null : unquote(info.getSSID());
        Map<String, ScanResult> best = new HashMap<>();
        for (ScanResult r : wifi.getScanResults()) {
            if (r.SSID.isEmpty() || r.capabilities.contains("EAP") || r.capabilities.contains("WEP")) continue;
            ScanResult b = best.get(r.SSID);
            if (b == null || r.level > b.level) best.put(r.SSID, r);
        }
        Set<String> saved = new HashSet<>();
        for (WifiConfiguration c : wifi.getConfiguredNetworks()) saved.add(unquote(c.SSID));
        List<ScanResult> nearby = new ArrayList<>(best.values());
        Collections.sort(nearby, new Comparator<ScanResult>() {
            public int compare(ScanResult a, ScanResult b) { return b.level - a.level; }
        });
        JSONArray networks = new JSONArray();
        for (ScanResult r : nearby) {
            networks.put(new JSONObject()
                    .put("ssid", r.SSID)
                    .put("secure", r.capabilities.contains("WPA"))
                    .put("saved", saved.contains(r.SSID))
                    .put("bars", WifiManager.calculateSignalLevel(r.level, 4)));
        }
        return new JSONObject().put("current", current).put("networks", networks);
    }

    /** Joins a network: a saved one with no password, or a new one (WPA or open). */
    boolean connect(String ssid, String password) {
        int id = -1;
        for (WifiConfiguration c : wifi.getConfiguredNetworks()) {
            if (ssid.equals(unquote(c.SSID))) id = c.networkId;
        }
        if (password != null && !password.isEmpty() || id < 0) {
            WifiConfiguration c = new WifiConfiguration();
            c.SSID = "\"" + ssid + "\"";
            if (password == null || password.isEmpty()) c.allowedKeyManagement.set(WifiConfiguration.KeyMgmt.NONE);
            else c.preSharedKey = "\"" + password + "\"";
            id = wifi.addNetwork(c);
        }
        if (id < 0 || !wifi.enableNetwork(id, true)) return false;
        wifi.saveConfiguration();
        return wifi.reconnect();
    }

    private static String unquote(String ssid) {
        return ssid != null && ssid.length() > 1 && ssid.startsWith("\"") && ssid.endsWith("\"")
                ? ssid.substring(1, ssid.length() - 1) : ssid;
    }
}
