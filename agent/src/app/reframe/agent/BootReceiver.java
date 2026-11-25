package app.reframe.agent;

import android.content.BroadcastReceiver;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;

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
    }
}
