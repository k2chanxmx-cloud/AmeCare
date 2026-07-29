from __future__ import annotations

from datetime import date

from flask import Flask, redirect, render_template, request, url_for, flash

from config import config
from database import (
    get_report_period,
    list_report_periods,
    save_heart_log,
    list_train_logs_for_date,
    save_train_log,
    test_database_connection,
)
from weather import get_current_weather, weather_for_log
from ai_report import create_report_preview, generate_weekly_report, AIReportError

app = Flask(__name__)
app.secret_key = config.SECRET_KEY


@app.context_processor
def inject_globals():
    return {
        "app_name": config.APP_NAME,
    }


@app.route("/")
def index():
    weather = get_current_weather()

    period_start, period_end = get_report_period()

    preview = create_report_preview(
        period_start=period_start,
        period_end=period_end,
    )

    today = date.today()

    return render_template(
        "index.html",
        today=today,
        weather=weather,
        period_start=period_start,
        period_end=period_end,
        preview=preview,
    )


@app.route("/heart", methods=["GET", "POST"])
def heart():
    if request.method == "POST":
        save_heart_log(
            log_date=date.today(),
            heart_level=int(request.form["heart_level"]),
            anxiety_level=int(request.form["anxiety_level"]),
            good_things=request.form.get("good_things"),
            difficulties=request.form.get("difficulties"),
            memo=request.form.get("memo"),
            weather=weather_for_log(),
        )

        flash("今日の心を保存しました。", "success")
        return redirect(url_for("heart"))

    return render_template(
        "heart.html",
        weather=get_current_weather(),
    )


@app.route("/train", methods=["GET", "POST"])
def train():
    if request.method == "POST":
        from datetime import datetime

        save_train_log(
            logged_at=datetime.now(),
            log_date=date.today(),
            result=request.form["result"],
            travel_support=request.form.get("travel_support"),
            station_count=int(request.form["station_count"]) if request.form.get("station_count") else None,
            crowd_level=request.form.get("crowd_level"),
            panic_flag=request.form.get("panic_flag") == "on",
            failed_stage=request.form.get("failed_stage"),
            before_state=request.form.get("before_state"),
            during_state=request.form.get("during_state"),
            after_state=request.form.get("after_state"),
            memo=request.form.get("memo"),
            weather=weather_for_log(),
        )

        flash("電車ログを保存しました。", "success")
        return redirect(url_for("train"))

    return render_template(
        "train.html",
        logs=list_train_logs_for_date(date.today()),
        weather=get_current_weather(),
    )


@app.route("/reports")
def reports():
    return render_template(
        "reports.html",
        periods=list_report_periods(),
    )


@app.route("/reports/generate/<period_start>/<period_end>", methods=["POST"])
def generate(period_start, period_end):
    from datetime import datetime

    start = datetime.strptime(period_start, "%Y-%m-%d").date()
    end = datetime.strptime(period_end, "%Y-%m-%d").date()

    try:
        generate_weekly_report(
            period_start=start,
            period_end=end,
            overwrite=False,
        )
        flash("AI診察レポートを生成しました。", "success")
    except AIReportError as e:
        flash(str(e), "error")

    return redirect(url_for("reports"))


@app.route("/health")
def health():
    return {
        "status": "ok",
        "database": test_database_connection(),
    }


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True,
    )
