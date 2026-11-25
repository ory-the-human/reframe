package app.cozysync.agent;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;

/**
 * Starts the agent and closes at once, without showing anything. The frame's
 * firmware (Allwinner's BackgroundManagerService) refuses to start an app's
 * service until the app has opened an activity, so the agent starts through here.
 */
public class StartActivity extends Activity {
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        Intent service = new Intent(this, AgentService.class);
        if (getIntent().getExtras() != null) service.putExtras(getIntent().getExtras());
        startService(service);
        finish();
        overridePendingTransition(0, 0);
    }
}
