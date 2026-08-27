import os
import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import io
from datetime import datetime, timezone
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

st.set_page_config(
    page_title="ClickUp Task Exporter",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_URL = "https://api.clickup.com/api/v2"


def get_setting(name: str, default: str = "") -> str:
    """Read local environment variables or Streamlit Cloud secrets."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        secret_value = st.secrets.get(name, default)
        return str(secret_value) if secret_value else default
    except (FileNotFoundError, KeyError):
        return default


# ── AI client (Replit-managed OpenAI proxy) ───────────────────────────────────

_ai_client = None

def get_ai_client():
    global _ai_client
    if _ai_client is None:
        # Replit AI is used automatically in Replit. For local use, the
        # standard OPENAI_API_KEY and optional OPENAI_BASE_URL are supported.
        base_url = get_setting("AI_INTEGRATIONS_OPENAI_BASE_URL") or get_setting(
            "OPENAI_BASE_URL"
        )
        api_key = get_setting("AI_INTEGRATIONS_OPENAI_API_KEY") or get_setting(
            "OPENAI_API_KEY"
        )
        if api_key:
            client_args = {"api_key": api_key}
            if base_url:
                client_args["base_url"] = base_url
            _ai_client = OpenAI(**client_args)
    return _ai_client


# ── Security gate ─────────────────────────────────────────────────────────────

TEAM_ACCESS_CODE = get_setting("TEAM_ACCESS_CODE")

def check_access():
    """Return True if the user has passed the team access gate."""
    if not TEAM_ACCESS_CODE:
        return True  # No code configured — open access
    if st.session_state.get("access_granted"):
        return True
    return False


if not check_access():
    st.title("ClickUp Task Exporter")
    st.markdown("This tool is restricted to authorised team members.")
    code_input = st.text_input("Enter team access code", type="password")
    if st.button("Unlock", type="primary"):
        if code_input == TEAM_ACCESS_CODE:
            st.session_state["access_granted"] = True
            st.rerun()
        else:
            st.error("Incorrect access code. Please contact your administrator.")
    st.stop()


# ── ClickUp API helpers ───────────────────────────────────────────────────────

def get_headers(token: str) -> dict:
    return {"Authorization": token, "Content-Type": "application/json"}


@st.cache_data(show_spinner=False)
def fetch_current_user(token: str):
    try:
        r = requests.get(f"{BASE_URL}/user", headers=get_headers(token), timeout=10)
        r.raise_for_status()
        return r.json().get("user", {})
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            return None
        raise


@st.cache_data(show_spinner=False)
def fetch_workspaces(token: str):
    try:
        r = requests.get(f"{BASE_URL}/team", headers=get_headers(token), timeout=10)
        r.raise_for_status()
        return r.json().get("teams", [])
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            return None
        raise


@st.cache_data(show_spinner=False)
def fetch_spaces(token: str, team_id: str):
    r = requests.get(
        f"{BASE_URL}/team/{team_id}/space?archived=false",
        headers=get_headers(token), timeout=10,
    )
    r.raise_for_status()
    return r.json().get("spaces", [])


@st.cache_data(show_spinner=False)
def fetch_lists(token: str, space_id: str):
    lists = []
    r = requests.get(
        f"{BASE_URL}/space/{space_id}/list?archived=false",
        headers=get_headers(token), timeout=10,
    )
    r.raise_for_status()
    lists.extend(r.json().get("lists", []))

    r2 = requests.get(
        f"{BASE_URL}/space/{space_id}/folder?archived=false",
        headers=get_headers(token), timeout=10,
    )
    r2.raise_for_status()
    for folder in r2.json().get("folders", []):
        r3 = requests.get(
            f"{BASE_URL}/folder/{folder['id']}/list?archived=false",
            headers=get_headers(token), timeout=10,
        )
        r3.raise_for_status()
        for lst in r3.json().get("lists", []):
            lst["_folder_name"] = folder["name"]
            lists.append(lst)
    return lists


@st.cache_data(show_spinner=False)
def fetch_tasks(token: str, list_id: str, include_closed: bool, assignee_id: int = None):
    tasks = []
    page = 0
    while True:
        params = {
            "page": page,
            "include_closed": str(include_closed).lower(),
            "subtasks": "true",
        }
        if assignee_id is not None:
            params["assignees[]"] = assignee_id
        r = requests.get(
            f"{BASE_URL}/list/{list_id}/task",
            headers=get_headers(token), params=params, timeout=15,
        )
        r.raise_for_status()
        data = r.json().get("tasks", [])
        if not data:
            break
        tasks.extend(data)
        page += 1
        if len(data) < 100:
            break

    if assignee_id is not None:
        tasks = [
            t for t in tasks
            if any(str(a.get("id")) == str(assignee_id) for a in t.get("assignees", []))
        ]
    return tasks


# ── Data formatting ───────────────────────────────────────────────────────────

def format_assignees(task: dict) -> str:
    return ", ".join(
        a.get("username", a.get("email", "")) for a in task.get("assignees", [])
    )


def format_tags(task: dict) -> str:
    return ", ".join(tag.get("name", "") for tag in task.get("tags", []))


def format_due_date(task: dict) -> str:
    due = task.get("due_date")
    if not due:
        return ""
    try:
        return datetime.fromtimestamp(int(due) / 1000).strftime("%Y-%m-%d")
    except Exception:
        return ""


def format_priority(task: dict) -> str:
    p = task.get("priority")
    if not p:
        return "None"
    mapping = {"1": "Urgent", "2": "High", "3": "Normal", "4": "Low"}
    pid = str(p.get("id", ""))
    return mapping.get(pid, p.get("priority", "None"))


def is_overdue(task: dict) -> bool:
    due = task.get("due_date")
    if not due:
        return False
    try:
        due_ts = int(due) / 1000
        now_ts = datetime.now(timezone.utc).timestamp()
        status = task.get("status", {}).get("type", "").lower()
        return due_ts < now_ts and status not in ("closed", "done", "complete")
    except Exception:
        return False


def tasks_to_dataframe(tasks: list) -> pd.DataFrame:
    rows = []
    for t in tasks:
        rows.append({
            "Task Name": t.get("name", ""),
            "Status": t.get("status", {}).get("status", "").title(),
            "Assignees": format_assignees(t),
            "Priority": format_priority(t),
            "Tags": format_tags(t),
            "Due Date": format_due_date(t),
            "Overdue": "Yes" if is_overdue(t) else "No",
            "URL": t.get("url", ""),
        })
    return pd.DataFrame(rows)


# ── Export helpers ────────────────────────────────────────────────────────────

def to_csv(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def to_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Tasks")
        workbook = writer.book
        worksheet = writer.sheets["Tasks"]
        header_fmt = workbook.add_format({
            "bold": True, "bg_color": "#7C3AED",
            "font_color": "#FFFFFF", "border": 1,
        })
        for col_num, col_name in enumerate(df.columns):
            worksheet.write(0, col_num, col_name, header_fmt)
            max_len = max(df[col_name].astype(str).map(len).max(), len(col_name)) + 4
            worksheet.set_column(col_num, col_num, min(max_len, 60))
    return buf.getvalue()


# ── AI insights ───────────────────────────────────────────────────────────────

def build_stats_summary(df: pd.DataFrame) -> str:
    total = len(df)
    overdue = int((df["Overdue"] == "Yes").sum())
    status_counts = df["Status"].value_counts().to_dict()
    priority_counts = df["Priority"].value_counts().to_dict()
    unassigned = int((df["Assignees"] == "").sum())
    no_due = int((df["Due Date"] == "").sum())

    tag_counts: dict = {}
    for tags in df["Tags"]:
        for t in [x.strip() for x in tags.split(",") if x.strip()]:
            tag_counts[t] = tag_counts.get(t, 0) + 1

    # Per-person summary for the AI prompt
    person_rows = []
    expanded = (
        df.assign(Assignee=df["Assignees"].replace("", "Unassigned"))
          .assign(Assignee=df["Assignees"].str.split(", "))
          .explode("Assignees")
          .rename(columns={"Assignees": "Assignee"})
    )
    for person, grp in expanded.groupby("Assignee"):
        top_tag = (
            grp["Tags"].str.split(", ").explode().replace("", pd.NA)
            .dropna().value_counts()
        )
        top_tag_str = top_tag.index[0] if len(top_tag) else "none"
        top_priority = grp["Priority"].value_counts()
        top_prio_str = top_priority.index[0] if len(top_priority) else "none"
        overdue_n = int((grp["Overdue"] == "Yes").sum())
        person_rows.append(
            f"  {person}: {len(grp)} tasks, top tag={top_tag_str}, "
            f"top priority={top_prio_str}, overdue={overdue_n}"
        )

    lines = [
        f"Total tasks: {total}",
        f"Overdue tasks: {overdue}",
        f"Unassigned tasks: {unassigned}",
        f"Tasks without a due date: {no_due}",
        f"Status breakdown: {status_counts}",
        f"Priority breakdown: {priority_counts}",
        f"Top tags: {dict(sorted(tag_counts.items(), key=lambda x: -x[1])[:10])}",
        "Per-person breakdown:",
        *person_rows,
    ]
    return "\n".join(lines)


@st.cache_data(show_spinner=False)
def generate_ai_insights(stats_summary: str, list_name: str) -> str:
    client = get_ai_client()
    if client is None:
        return ""
    try:
        response = client.chat.completions.create(
            model="gpt-5-mini",
            max_completion_tokens=600,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a project management analyst. "
                        "Given task statistics from a ClickUp list, write a concise, "
                        "actionable analysis in plain prose — no bullet points, no markdown headers, "
                        "no emojis. Three short paragraphs maximum. "
                        "Focus on: (1) workload health and risk areas, "
                        "(2) priority distribution and whether it looks balanced, "
                        "(3) the top one or two concrete actions the team should take this week."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"List name: {list_name}\n\n"
                        f"Task statistics:\n{stats_summary}"
                    ),
                },
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"AI analysis unavailable: {e}"


# ── Analytics helpers ─────────────────────────────────────────────────────────

PRIORITY_ORDER = ["Urgent", "High", "Normal", "Low", "None"]
PRIORITY_COLORS = {
    "Urgent": "#EF4444", "High": "#F97316",
    "Normal": "#A78BFA", "Low": "#6EE7B7", "None": "#6B7280",
}
CHART_LAYOUT = dict(
    margin=dict(l=0, r=0, t=10, b=0),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    template="plotly_dark",
)


def explode_assignees(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with one row per assignee (multi-assignee tasks duplicated)."""
    out = df.copy()
    out["Assignee"] = out["Assignees"].replace("", "Unassigned")
    out = out.assign(Assignee=out["Assignee"].str.split(", ")).explode("Assignee")
    return out


def hbar(data: pd.DataFrame, x: str, y: str, color_col: str = None,
         color_scale: str = "Purples", color_map: dict = None) -> go.Figure:
    """Compact horizontal bar chart, dark transparent background."""
    if color_map:
        fig = px.bar(data, x=x, y=y, orientation="h",
                     color=color_col, color_discrete_map=color_map,
                     barmode="stack", template="plotly_dark")
    else:
        fig = px.bar(data, x=x, y=y, orientation="h",
                     color=x, color_continuous_scale=color_scale,
                     template="plotly_dark")
        fig.update_layout(coloraxis_showscale=False)
    fig.update_layout(**CHART_LAYOUT, showlegend=bool(color_map),
                      legend=dict(orientation="h", y=-0.2, x=0))
    return fig


# ── Analytics section ─────────────────────────────────────────────────────────

def render_analytics(df: pd.DataFrame, list_name: str):
    st.markdown("### Task Analytics")

    total = len(df)
    overdue = int((df["Overdue"] == "Yes").sum())
    unassigned = int((df["Assignees"] == "").sum())
    no_due = int((df["Due Date"] == "").sum())

    # ── Headline numbers ──────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Tasks", total)
    c2.metric("Overdue", overdue,
              delta=f"{round(overdue / total * 100)}%" if total else "0%",
              delta_color="inverse")
    c3.metric("Unassigned", unassigned)
    c4.metric("No Due Date", no_due)

    st.markdown("---")

    # ── Overview row: status + priority ──────────────────────────────────────
    st.markdown("#### Overview")
    ov1, ov2 = st.columns(2)

    with ov1:
        st.markdown("**Status distribution**")
        status_df = df["Status"].value_counts().reset_index()
        status_df.columns = ["Status", "Count"]
        st.plotly_chart(hbar(status_df, "Count", "Status"), use_container_width=True)
        st.caption(
            "A healthy list has most tasks actively moving — In Progress or In Review. "
            "A large To Do block means work is queued but not started."
        )

    with ov2:
        st.markdown("**Priority distribution**")
        priority_df = (
            df["Priority"].value_counts()
            .reindex(PRIORITY_ORDER).dropna().reset_index()
        )
        priority_df.columns = ["Priority", "Count"]
        fig_pie = px.pie(
            priority_df, values="Count", names="Priority",
            color="Priority", color_discrete_map=PRIORITY_COLORS,
            template="plotly_dark", hole=0.45,
        )
        fig_pie.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=-0.1),
        )
        st.plotly_chart(fig_pie, use_container_width=True)
        st.caption(
            "Normal and High should be the majority. A large Urgent slice means "
            "the team is in reactive mode. A large None slice means tasks need triage."
        )

    st.markdown("---")

    # ── Per-person workload ───────────────────────────────────────────────────
    st.markdown("#### Team Workload")

    exp_df = explode_assignees(df)

    # Stacked bar: Assignee × Priority
    prio_cross = (
        exp_df.groupby(["Assignee", "Priority"]).size().reset_index(name="Count")
    )
    # Sort by total tasks descending
    order = (
        prio_cross.groupby("Assignee")["Count"].sum()
        .sort_values(ascending=True).index.tolist()
    )
    prio_cross["Assignee"] = pd.Categorical(prio_cross["Assignee"], categories=order, ordered=True)
    prio_cross = prio_cross.sort_values("Assignee")

    fig_team = px.bar(
        prio_cross, x="Count", y="Assignee", color="Priority",
        orientation="h", barmode="stack",
        color_discrete_map=PRIORITY_COLORS,
        template="plotly_dark",
    )
    fig_team.update_layout(
        **CHART_LAYOUT,
        legend=dict(orientation="h", y=-0.15, x=0),
        height=max(200, len(order) * 38),
    )
    st.plotly_chart(fig_team, use_container_width=True)
    st.caption(
        "Each bar is one person's total task count, broken down by priority. "
        "A person with a large red (Urgent) segment has a stressful week. "
        "Comparing bar lengths shows who is carrying the most load."
    )

    # Stacked bar: Assignee × Tag (only if tags exist)
    all_tags = [t.strip() for tags in df["Tags"] for t in tags.split(",") if t.strip()]
    if all_tags:
        st.markdown("**What type of work does each person do?**")

        # Explode tags too — one row per (assignee, tag)
        tag_exp = exp_df.copy()
        tag_exp["Tag"] = tag_exp["Tags"].replace("", "Untagged")
        tag_exp = tag_exp.assign(Tag=tag_exp["Tag"].str.split(", ")).explode("Tag")
        tag_exp["Tag"] = tag_exp["Tag"].replace("", "Untagged")

        # Keep only top N tags to avoid chart clutter
        top_tags = pd.Series(all_tags).value_counts().head(8).index.tolist()
        tag_exp["Tag"] = tag_exp["Tag"].where(tag_exp["Tag"].isin(top_tags), other="Other")

        tag_cross = (
            tag_exp.groupby(["Assignee", "Tag"]).size().reset_index(name="Count")
        )
        tag_cross["Assignee"] = pd.Categorical(
            tag_cross["Assignee"], categories=order, ordered=True
        )
        tag_cross = tag_cross.sort_values("Assignee")

        fig_tags = px.bar(
            tag_cross, x="Count", y="Assignee", color="Tag",
            orientation="h", barmode="stack",
            template="plotly_dark",
            color_discrete_sequence=px.colors.qualitative.Pastel,
        )
        fig_tags.update_layout(
            **CHART_LAYOUT,
            legend=dict(orientation="h", y=-0.15, x=0),
            height=max(200, len(order) * 38),
        )
        st.plotly_chart(fig_tags, use_container_width=True)
        st.caption(
            "Shows the type of work each person is primarily handling, based on task tags. "
            "A person whose bar is mostly one colour is specialised. "
            "A very mixed bar means they are context-switching across different work types."
        )
    else:
        st.caption(
            "No tags are set on tasks in this list. Add tags in ClickUp to see "
            "a breakdown of what type of work each person handles."
        )

    st.markdown("---")

    # ── Person Focus ─────────────────────────────────────────────────────────
    st.markdown("#### Person Focus")

    people = sorted(exp_df["Assignee"].unique().tolist())
    selected_person = st.selectbox("Select a team member", people)

    person_df = exp_df[exp_df["Assignee"] == selected_person]
    p_total = len(person_df)
    p_overdue = int((person_df["Overdue"] == "Yes").sum())
    p_no_due = int((person_df["Due Date"] == "").sum())

    pm1, pm2, pm3 = st.columns(3)
    pm1.metric("Tasks", p_total)
    pm2.metric("Overdue", p_overdue,
               delta=f"{round(p_overdue / p_total * 100)}%" if p_total else "0%",
               delta_color="inverse")
    pm3.metric("No Due Date", p_no_due)

    pf1, pf2 = st.columns(2)

    with pf1:
        st.markdown(f"**{selected_person}'s tasks by status**")
        p_status = person_df["Status"].value_counts().reset_index()
        p_status.columns = ["Status", "Count"]
        st.plotly_chart(hbar(p_status, "Count", "Status"), use_container_width=True)

    with pf2:
        st.markdown(f"**{selected_person}'s tasks by priority**")
        p_prio = (
            person_df["Priority"].value_counts()
            .reindex(PRIORITY_ORDER).dropna().reset_index()
        )
        p_prio.columns = ["Priority", "Count"]
        fig_pp = px.pie(
            p_prio, values="Count", names="Priority",
            color="Priority", color_discrete_map=PRIORITY_COLORS,
            template="plotly_dark", hole=0.45,
        )
        fig_pp.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=-0.1),
        )
        st.plotly_chart(fig_pp, use_container_width=True)

    if all_tags:
        person_tags = [
            t.strip()
            for tags in person_df["Tags"]
            for t in tags.split(",")
            if t.strip()
        ]
        if person_tags:
            st.markdown(f"**{selected_person}'s top work types (by tag)**")
            pt_df = pd.Series(person_tags).value_counts().head(10).reset_index()
            pt_df.columns = ["Tag", "Count"]
            fig_pt = px.bar(
                pt_df, x="Count", y="Tag", orientation="h",
                color="Count", color_continuous_scale="Teal",
                template="plotly_dark",
            )
            fig_pt.update_layout(**CHART_LAYOUT, coloraxis_showscale=False)
            st.plotly_chart(fig_pt, use_container_width=True)

    if p_overdue > 0:
        with st.expander(f"View {selected_person}'s overdue tasks ({p_overdue})"):
            overdue_cols = ["Task Name", "Status", "Due Date", "Priority"]
            st.dataframe(
                person_df[person_df["Overdue"] == "Yes"][overdue_cols],
                use_container_width=True, hide_index=True,
            )

    st.markdown("---")

    # ── Team summary table ────────────────────────────────────────────────────
    st.markdown("#### Team Summary Table")

    summary_rows = []
    for person, grp in exp_df.groupby("Assignee"):
        person_tag_list = [
            t.strip()
            for tags in grp["Tags"]
            for t in tags.split(",")
            if t.strip()
        ]
        top_tag = (
            pd.Series(person_tag_list).value_counts().index[0]
            if person_tag_list else "—"
        )
        top_prio = grp["Priority"].value_counts().index[0] if len(grp) else "—"
        summary_rows.append({
            "Name": person,
            "Tasks": len(grp),
            "Overdue": int((grp["Overdue"] == "Yes").sum()),
            "Top Priority": top_prio,
            "Top Tag": top_tag,
        })

    summary_df = pd.DataFrame(summary_rows).sort_values("Tasks", ascending=False)
    st.dataframe(summary_df, use_container_width=True, hide_index=True)
    st.caption(
        "A quick reference for the whole team. Sort by Overdue to find who needs support, "
        "or by Top Tag to see how work specialisation is distributed."
    )

    st.markdown("---")

    # ── Overdue detail ────────────────────────────────────────────────────────
    if overdue > 0:
        st.markdown("#### Overdue Tasks")
        overdue_df = df[df["Overdue"] == "Yes"][
            ["Task Name", "Status", "Assignees", "Due Date", "Priority"]
        ]
        st.dataframe(overdue_df, use_container_width=True, hide_index=True)

    # ── AI narrative ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### AI Analysis")
    with st.spinner("Generating analysis..."):
        stats_summary = build_stats_summary(df)
        insight = generate_ai_insights(stats_summary, list_name)
    if insight:
        st.markdown(insight)
    else:
        st.info("AI analysis is not available. Ensure the AI integration env vars are set.")
    st.caption(
        "Generated from the current task snapshot. Click 'Clear Cache' in the sidebar "
        "to refresh after updating tasks in ClickUp."
    )


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("ClickUp Task Exporter")
    st.markdown("---")

    token = st.text_input(
        "Personal API Token",
        type="password",
        placeholder="pk_xxxxxxxxxx...",
        help="Your ClickUp personal API token",
    )

    with st.expander("How to find my token"):
        st.markdown(
            """
**Steps to get your token:**

1. Log in to [ClickUp](https://app.clickup.com)
2. Click your avatar in the bottom-left corner
3. Go to **Settings** then **Apps**
4. Under **API Token**, click **Generate** (or copy your existing token)
5. Paste it in the field above

Your token starts with `pk_` and is unique to your account.
It is never stored — only used in this session.
"""
        )

    if token:
        st.markdown("---")
        if st.button("Clear Cache", use_container_width=True, type="secondary"):
            st.cache_data.clear()
            st.rerun()

    st.markdown("---")
    st.caption("Data is fetched directly from the ClickUp API and never stored.")


# ── Auth gate ─────────────────────────────────────────────────────────────────

if not token:
    st.markdown(
        """
        <div style="display:flex; flex-direction:column; align-items:center;
                    justify-content:center; height:70vh; text-align:center;">
            <h1 style="margin-bottom:8px;">ClickUp Task Exporter</h1>
            <p style="color:#888; font-size:1.1rem; max-width:400px;">
                Enter your ClickUp Personal API Token in the sidebar to get started.
                Your data is never stored — everything stays in your browser session.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()


# ── Connect ───────────────────────────────────────────────────────────────────

with st.spinner("Connecting to ClickUp..."):
    try:
        current_user = fetch_current_user(token)
        workspaces = fetch_workspaces(token)
    except Exception as e:
        st.error(f"Failed to connect to ClickUp. Please check your token. ({e})")
        st.stop()

if current_user is None or workspaces is None:
    st.error("Invalid API token. Please double-check your ClickUp Personal API Token and try again.")
    st.stop()

if not workspaces:
    st.warning("No workspaces found for this account.")
    st.stop()

current_user_id = current_user.get("id")
current_user_name = current_user.get("username") or current_user.get("email", "you")

# ── Header ────────────────────────────────────────────────────────────────────

st.title("ClickUp Task Exporter")
st.markdown(
    f"Signed in as **{current_user_name}** &nbsp;·&nbsp; "
    "Select a workspace, space, and list below."
)
st.markdown("---")

# ── Dropdowns ─────────────────────────────────────────────────────────────────

col1, col2, col3 = st.columns(3)

with col1:
    ws_options = {ws["name"]: ws["id"] for ws in workspaces}
    selected_ws_name = st.selectbox("Workspace", list(ws_options.keys()))
    selected_ws_id = ws_options[selected_ws_name]

selected_space_id = None
selected_list_id = None
selected_list_name = ""

with col2:
    with st.spinner("Loading spaces..."):
        try:
            spaces = fetch_spaces(token, selected_ws_id)
        except Exception as e:
            st.error(f"Could not load spaces: {e}")
            spaces = []
    if spaces:
        space_options = {s["name"]: s["id"] for s in spaces}
        selected_space_name = st.selectbox("Space", list(space_options.keys()))
        selected_space_id = space_options[selected_space_name]
    else:
        st.selectbox("Space", ["No spaces found"], disabled=True)

with col3:
    if selected_space_id:
        with st.spinner("Loading lists..."):
            try:
                lists = fetch_lists(token, selected_space_id)
            except Exception as e:
                st.error(f"Could not load lists: {e}")
                lists = []
        if lists:
            list_labels = {}
            for lst in lists:
                label = lst["name"]
                if "_folder_name" in lst:
                    label = f"{lst['_folder_name']} / {lst['name']}"
                list_labels[label] = lst["id"]
                if lst["id"] not in [v for v in list_labels.values()][:-1]:
                    selected_list_name = label
            selected_list_label = st.selectbox("List", list(list_labels.keys()))
            selected_list_id = list_labels[selected_list_label]
            selected_list_name = selected_list_label
        else:
            st.selectbox("List", ["No lists found"], disabled=True)
    else:
        st.selectbox("List", ["Select a space first"], disabled=True)

if not selected_list_id:
    st.info("Select a workspace, space, and list above to load tasks.")
    st.stop()

st.markdown("---")

# ── Fetch filters ─────────────────────────────────────────────────────────────

filter_col1, filter_col2 = st.columns(2)

with filter_col1:
    include_closed = st.checkbox(
        "Include closed / archived tasks",
        value=False,
        help="When enabled, tasks in closed statuses are also fetched.",
    )

with filter_col2:
    my_tasks_only = st.checkbox(
        f"My Tasks only ({current_user_name})",
        value=False,
        help=(
            "Filters tasks where you are one of the assignees. "
            "Tasks assigned to you and others are still included."
        ),
    )

assignee_filter_id = current_user_id if my_tasks_only else None

with st.spinner("Fetching tasks from ClickUp..."):
    try:
        tasks = fetch_tasks(token, selected_list_id, include_closed, assignee_filter_id)
    except Exception as e:
        st.error(f"Failed to fetch tasks: {e}")
        st.stop()

if not tasks:
    if my_tasks_only:
        st.info(f"No tasks assigned to {current_user_name} were found in this list.")
    else:
        st.info("No tasks found in this list.")
    st.stop()

df = tasks_to_dataframe(tasks)
filter_label = f"My Tasks — {selected_list_name}" if my_tasks_only else selected_list_name

# ── Tabs: Tasks / Analytics ───────────────────────────────────────────────────

tab_tasks, tab_analytics = st.tabs(["Task Table", "Analytics"])

with tab_tasks:
    st.markdown(f"#### Tasks in **{filter_label}**")

    search = st.text_input("Search tasks", placeholder="Filter by name, status, assignee, tag...")

    if search:
        mask = df.apply(
            lambda row: row.astype(str).str.contains(search, case=False).any(), axis=1
        )
        filtered_df = df[mask]
    else:
        filtered_df = df

    display_df = filtered_df.drop(columns=["URL"])

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        height=min(600, 50 + len(display_df) * 35),
    )

    if search and len(filtered_df) < len(df):
        st.caption(f'Showing {len(filtered_df)} of {len(df)} tasks matching "{search}"')

    st.markdown("---")
    st.markdown("#### Export")
    dl_col1, dl_col2 = st.columns(2)
    safe_name = filter_label.replace(" / ", "_").replace(" ", "_")

    with dl_col1:
        st.download_button(
            label="Download as CSV",
            data=to_csv(filtered_df),
            file_name=f"clickup_{safe_name}.csv",
            mime="text/csv",
            use_container_width=True,
            type="primary",
        )
    with dl_col2:
        st.download_button(
            label="Download as Excel",
            data=to_excel(filtered_df),
            file_name=f"clickup_{safe_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="secondary",
        )

with tab_analytics:
    render_analytics(df, filter_label)
