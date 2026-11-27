package app.reframe.agent;

import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;

/** Starts the agent when the frame boots, when Wi-Fi connects and after the app is updated. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        ComponentName started = context.startService(new Intent(context, AgentService.class));
        if (started == null) {
            // Refused by the firmware's background manager: go through an activity.
            context.startActivity(new Intent(context, StartActivity.class)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_ANIMATION
                            | Intent.FLAG_ACTIVITY_EXCLUDE_FROM_RECENTS | Intent.FLAG_ACTIVITY_NO_HISTORY));
        }
        if (Intent.ACTION_MY_PACKAGE_REPLACED.equals(intent.getAction())) restartKiosk(context);
    }

    /** The open page may predate this agent: reopen the kiosk (the home app) so it loads the current one. */
    private static void restartKiosk(Context context) {
        final String home = context.getPackageManager().resolveActivity(
                new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), PackageManager.MATCH_DEFAULT_ONLY)
                .activityInfo.packageName;
        new Thread(new Runnable() {
            @Override
            public void run() {
                Root.run("am force-stop " + home);
                Root.run("am start -a android.intent.action.MAIN -c android.intent.category.HOME");
            }
        }).start();
    }
}
