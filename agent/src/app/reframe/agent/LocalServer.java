package app.reframe.agent;

import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URLDecoder;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Serves the Mirror's copy of the slideshow on 127.0.0.1, with the server's URL
 * layout (/show/<key>, /playlist, /media/<name>), for the kiosk, plus the page's
 * settings panel (POST /settings, GET and POST /wifi) and whether the screen is on (GET /screen). The key isn't checked: only apps on the
 * frame can connect, and they can all be root.
 */
final class LocalServer extends Thread {
    static final int PORT = 1992;
    private static final Pattern PATH = Pattern.compile("/show/[^/?]+(/playlist|/media/([^/?]+)|/settings|/wifi|/screen)?/?(\\?.*)?");
    private static final Pattern RANGE = Pattern.compile("bytes=(\\d+)-(\\d*)");

    private final Mirror mirror;
    private final Wifi wifi;
    private final Screen screen;

    LocalServer(Mirror mirror, Wifi wifi, Screen screen) {
        super(AgentService.TAG + "-http");
        this.mirror = mirror;
        this.wifi = wifi;
        this.screen = screen;
    }

    public void run() {
        try {
            ServerSocket server = new ServerSocket(PORT, 16, InetAddress.getByName("127.0.0.1"));
            while (true) {
                final Socket s = server.accept();
                new Thread(new Runnable() {
                    public void run() {
                        try {
                            serve(s);
                        } catch (Exception e) {
                            Log.w(AgentService.TAG, "local server: " + e);
                        } finally {
                            try {
                                s.close();
                            } catch (Exception ignored) {
                            }
                        }
                    }
                }).start();
            }
        } catch (Exception e) {
            Log.e(AgentService.TAG, "local server stopped", e);
        }
    }

    private void serve(Socket s) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), "ISO-8859-1"));
        String[] request = in.readLine().split(" ");
        String range = null;
        int length = 0;
        for (String line; (line = in.readLine()) != null && !line.isEmpty(); ) {
            if (line.toLowerCase().startsWith("range:")) range = line.substring(6).trim();
            if (line.toLowerCase().startsWith("content-length:")) length = Integer.parseInt(line.substring(15).trim());
        }
        char[] raw = new char[length];
        for (int n = 0, r; n < length && (r = in.read(raw, n, length - n)) > 0; ) n += r;
        String body = new String(new String(raw).getBytes("ISO-8859-1"), "UTF-8");
        OutputStream out = s.getOutputStream();
        Matcher m = PATH.matcher(request[1]);
        if (!mirror.ready() || !m.matches()) {
            send(out, "404 Not Found", "text/plain", "Not found".getBytes("UTF-8"));
        } else if ("/settings".equals(m.group(1))) {
            mirror.change(new JSONObject(body));
            send(out, "200 OK", "application/json", "{\"ok\": true}".getBytes("UTF-8"));
        } else if ("/screen".equals(m.group(1))) {
            send(out, "200 OK", "application/json", ("{\"on\": " + screen.isOn() + "}").getBytes("UTF-8"));
        } else if ("/wifi".equals(m.group(1)) && "POST".equals(request[0])) {
            JSONObject j = new JSONObject(body);
            boolean ok = wifi.connect(j.getString("ssid"), j.optString("password"));
            send(out, "200 OK", "application/json", ("{\"ok\": " + ok + "}").getBytes("UTF-8"));
        } else if ("/wifi".equals(m.group(1))) {
            send(out, "200 OK", "application/json", wifi.list().toString().getBytes("UTF-8"));
        } else if (m.group(1) == null) {
            send(out, "200 OK", "text/html; charset=utf-8", mirror.page().getBytes("UTF-8"));
        } else if (m.group(2) == null) {
            send(out, "200 OK", "application/json", mirror.playlist().toString().getBytes("UTF-8"));
        } else {
            File f = mirror.file(URLDecoder.decode(m.group(2), "UTF-8"));
            if (f == null || !f.exists()) send(out, "404 Not Found", "text/plain", "Not found".getBytes("UTF-8"));
            else sendFile(out, f, range);
        }
        out.flush();
    }

    private static void send(OutputStream out, String status, String type, byte[] body) throws Exception {
        head(out, status, type, body.length, null);
        out.write(body);
    }

    /** Video in the WebView asks for byte ranges, and needs 206 answers. */
    private static void sendFile(OutputStream out, File f, String range) throws Exception {
        long size = f.length(), start = 0, end = size - 1;
        Matcher r = range == null ? null : RANGE.matcher(range);
        boolean partial = r != null && r.matches();
        if (partial) {
            start = Long.parseLong(r.group(1));
            if (!r.group(2).isEmpty()) end = Math.min(end, Long.parseLong(r.group(2)));
        }
        head(out, partial ? "206 Partial Content" : "200 OK", type(f.getName()), end - start + 1,
                partial ? "Content-Range: bytes " + start + "-" + end + "/" + size + "\r\n" : null);
        InputStream in = new FileInputStream(f);
        try {
            in.skip(start);
            copy(in, out, end - start + 1);
        } finally {
            in.close();
        }
    }

    private static void head(OutputStream out, String status, String type, long length, String extra) throws Exception {
        String h = "HTTP/1.1 " + status + "\r\nContent-Type: " + type + "\r\nContent-Length: " + length
                + "\r\nAccept-Ranges: bytes\r\nCache-Control: no-cache\r\nConnection: close\r\n"
                + (extra == null ? "" : extra) + "\r\n";
        out.write(h.getBytes("ISO-8859-1"));
    }

    private static String type(String name) {
        String n = name.toLowerCase();
        if (n.endsWith(".mp4")) return "video/mp4";
        if (n.endsWith(".webm")) return "video/webm";
        if (n.endsWith(".png")) return "image/png";
        if (n.endsWith(".webp")) return "image/webp";
        if (n.endsWith(".gif")) return "image/gif";
        return "image/jpeg";
    }

    static void copy(InputStream in, OutputStream out, long count) throws java.io.IOException {
        byte[] buf = new byte[64 * 1024];
        int n;
        while (count > 0 && (n = in.read(buf, 0, (int) Math.min(buf.length, count))) > 0) {
            out.write(buf, 0, n);
            count -= n;
        }
    }
}
