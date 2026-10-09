package app.arienx.arienx_mobile
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews
class ArienXWidget: AppWidgetProvider() {
 override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
  for (id in ids) {
   val view = RemoteViews(context.packageName, R.layout.arienx_widget)
   val launch = PendingIntent.getActivity(context, id, Intent(context, MainActivity::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
   view.setOnClickPendingIntent(R.id.open_arienx, launch)
   manager.updateAppWidget(id, view)
  }
 }
}
