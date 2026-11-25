package app.cozysync.agent;

import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;

/** Runs shell commands as root through the frame's su, which lets apps use it. */
final class Root {
    private Root() {}

    /** The command's output, or null if it failed. */
    static String run(String command) {
        try {
            Process p = Runtime.getRuntime().exec(new String[] {"su", "-c", command});
            p.getOutputStream().close();
            String out = read(p.getInputStream());
            int rc = p.waitFor();
            if (rc != 0) {
                Log.w(AgentService.TAG, "root command failed (" + rc + "): " + command);
                return null;
            }
            return out;
        } catch (Exception e) {
            Log.w(AgentService.TAG, "root command failed: " + command, e);
            return null;
        }
    }

    static String read(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        int n;
        while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
        in.close();
        return buf.toString("UTF-8");
    }
}
