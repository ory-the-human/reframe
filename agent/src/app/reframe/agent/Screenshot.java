package app.reframe.agent;

import android.util.Log;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** Captures the screen as root and uploads it, when the report's reply asks for it. */
final class Screenshot {
    private Screenshot() {}

    static void send(final String server, final String key) {
        new Thread(new Runnable() {
            public void run() {
                try {
                    Process p = Runtime.getRuntime().exec(new String[] {"su", "-c", "screencap -p"});
                    p.getOutputStream().close();
                    HttpURLConnection c = (HttpURLConnection) new URL(server + "/agent/v1/screenshot").openConnection();
                    try {
                        c.setConnectTimeout(10 * 1000);
                        c.setReadTimeout(30 * 1000);
                        c.setRequestProperty("Authorization", "Bearer " + key);
                        c.setRequestProperty("Content-Type", "image/png");
                        c.setDoOutput(true);
                        InputStream in = p.getInputStream();
                        OutputStream out = c.getOutputStream();
                        LocalServer.copy(in, out, Long.MAX_VALUE);
                        in.close();
                        out.close();
                        p.waitFor();
                        if (c.getResponseCode() != 200) throw new Exception("HTTP " + c.getResponseCode());
                    } finally {
                        c.disconnect();
                    }
                    Log.i(AgentService.TAG, "sent a screenshot");
                } catch (Exception e) {
                    Log.w(AgentService.TAG, "screenshot failed: " + e);
                }
            }
        }, AgentService.TAG + "-screenshot").start();
    }
}
