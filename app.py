from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime
from threading import Lock

from flask import Flask, flash, redirect, render_template, request, url_for

from ai_report import (
    AIReportError,
    create_report_preview,
    generate_missing_completed_reports,
)
from config import config
from database import (
    get_report_period,
    list_recent_heart_logs,
    list_recent_train_logs,
    list_report_periods,
    save_heart_log,
    save_train_log,
    test_database_connection,
)
from weather import get_current_weather, weather_for_log

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

_report_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="amecare-report")
_report_future: Future | None = None
_report_future_lock = Lock()


def _report_job() -> None:
    try:
        generate_missing_completed_reports()
    except Exception:
        app.logger.exception("AI診察レポートの自動生成に失敗しました。")


def schedule_report_generation() -> bool:
    global _report_future

    with _report_future_lock:
        if _report_future is not None and not _report_future.done():
            return False

        _report_future = _report_executor.submit(_report_job)
        return True


@app.context_processor
def inject_globals():
    return {"app_name": config.APP_NAME}


@app.route("/")
def index():
    schedule_report_generation()

    weather = get_current_weather()
    period_start, period_end = get_report_period()
    preview = create_report_preview(period_start=period_start, period_end=period_end)

    return render_template(
        "index.html",
        today=date.today(),
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
        schedule_report_generation()
        flash("今日の心を保存しました。", "success")
        return redirect(url_for("heart"))

    return render_template(
        "heart.html",
        logs=list_recent_heart_logs(limit=30),
        weather=get_current_weather(),
    )


@app.route("/train", methods=["GET", "POST"])
def train():
    if request.method == "POST":
        station_count_raw = request.form.get("station_count")

        save_train_log(
            logged_at=datetime.now(),
            log_date=date.today(),
            result=request.form["result"],
            travel_support=request.form.get("travel_support"),
            station_count=int(station_count_raw) if station_count_raw else None,
            crowd_level=request.form.get("crowd_level"),
            panic_flag=request.form.get("panic_flag") == "on",
            failed_stage=request.form.get("failed_stage"),
            before_state=request.form.get("before_state"),
            during_state=request.form.get("during_state"),
            after_state=request.form.get("after_state"),
            memo=request.form.get("memo"),
            weather=weather_for_log(),
        )
        schedule_report_generation()
        flash("電車ログを保存しました。", "success")
        return redirect(url_for("train"))

    return render_template(
        "train.html",
        logs=list_recent_train_logs(limit=30),
        weather=get_current_weather(),
    )


@app.route("/reports")
def reports():
    generation_started = schedule_report_generation()
    period_previews = []

    for period in list_report_periods():
        preview = create_report_preview(
            period_start=period["period_start"],
            period_end=period["period_end"],
        )
        preview["is_current"] = period["is_current"]
        preview["is_complete"] = period["is_complete"]
        period_previews.append(preview)

    return render_template(
        "reports.html",
        periods=period_previews,
        generation_started=generation_started,
    )


@app.route("/reports/generate/<period_start>/<period_end>", methods=["POST"])
def generate(period_start: str, period_end: str):
    try:
        start = datetime.strptime(period_start, "%Y-%m-%d").date()
        end = datetime.strptime(period_end, "%Y-%m-%d").date()
        preview = create_report_preview(period_start=start, period_end=end)

        if preview["existing_report"]:
            flash("この期間のレポートは作成済みです。", "success")
        else:
            schedule_report_generation()
            flash(
                "AI診察レポートを自動作成しています。少し時間をおいて開き直してください。",
                "success",
            )
    except (ValueError, AIReportError) as exc:
        flash(str(exc), "error")

    return redirect(url_for("reports"))


@app.route("/health")
def health():
    return {"status": "ok", "database": test_database_connection()}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
