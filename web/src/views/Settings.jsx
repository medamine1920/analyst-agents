import { linkTo } from "../lib/route";
import { formatNumber } from "../lib/format";
import Admin from "./Admin";
import Evals from "./Evals";
import Display from "./settings/Display";
import Feedback from "./settings/Feedback";
import ModelKeys from "./settings/ModelKeys";
import Security from "./settings/Security";

function Profile({ session }) {
  return (
    <dl className="max-w-lg divide-y divide-rule rounded-xl border border-rule bg-surface text-sm">
      {[
        ["Name", session?.user],
        ["Role", session?.role === "admin" ? "Admin: invites people and manages accounts" : "User"],
        ["Two-step sign-in", session?.two_factor ? "On" : "Off (turn it on under Security)"],
      ].map(([label, value]) => (
        <div key={label} className="flex justify-between gap-4 px-5 py-3">
          <dt className="text-muted">{label}</dt>
          <dd className="text-right">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** The day's shared budget, kept here rather than on every page. */
function Usage({ session }) {
  const usage = session?.usage;
  if (!usage) return <p className="text-muted">No usage to show.</p>;
  const left = usage.tokens_left / Math.max(usage.daily_tokens, 1);
  return (
    <div className="max-w-lg space-y-4 rounded-xl border border-rule bg-surface p-5 text-sm">
      <div className="space-y-1.5">
        <p className="flex justify-between">
          <span>Model tokens left today</span>
          <span className="font-semibold">
            {formatNumber(usage.tokens_left)} of {formatNumber(usage.daily_tokens)}
          </span>
        </p>
        <div className="h-2 overflow-hidden rounded-full bg-meter-track" role="meter" aria-label="Model tokens left today" aria-valuemin={0} aria-valuemax={usage.daily_tokens} aria-valuenow={usage.tokens_left}>
          <div className={`h-full rounded-full ${left < 0.1 ? "bg-fail" : "bg-series-1"}`} style={{ width: `${left * 100}%` }} />
        </div>
      </div>
      {usage.my_daily_tokens != null && (
        <p className="flex justify-between">
          <span>Your share today</span>
          <span className="font-semibold">
            {formatNumber(usage.my_tokens_today)} of {formatNumber(usage.my_daily_tokens)} tokens
          </span>
        </p>
      )}
      <p className="flex justify-between">
        <span>Your questions in the last hour</span>
        <span className="font-semibold">
          {usage.questions_last_hour} of {usage.questions_per_hour}
        </span>
      </p>
      <p className="text-muted">
        The budget is shared by everyone on this console and starts again at midnight UTC. Saved answers to repeated
        questions cost nothing, and questions run on your own model key (under API keys) count against neither the
        budget nor your share.
      </p>
    </div>
  );
}

const TABS = [
  { id: "profile", label: "Profile", component: Profile },
  { id: "security", label: "Security", component: Security },
  { id: "keys", label: "API keys", component: ModelKeys, hint: "Your own keys for Claude, GPT, Gemini or Groq. Your questions run on the model you choose." },
  { id: "display", label: "Display", component: Display, hint: "How numbers look: colours for gains and losses." },
  { id: "usage", label: "Usage", component: Usage },
  { id: "people", label: "People", component: Admin, admin: true },
  { id: "feedback", label: "Feedback", component: Feedback, admin: true, hint: "What testers said about the answers." },
  { id: "developer", label: "Developer", component: Evals, admin: true, hint: "How often the agent is right, measured on fixed question sets." },
];

/** Settings in one place: who you are, how you sign in, your model keys, usage, and admin pages. */
export default function Settings({ params = {}, session, onSessionChanged }) {
  const tabs = TABS.filter((t) => !t.admin || session?.role === "admin");
  const tab = tabs.find((t) => t.id === params.tab) ?? tabs[0];
  const View = tab.component;
  return (
    <div className="grid grid-cols-1 gap-6 md:grid-cols-[11rem_minmax(0,1fr)]">
      <nav aria-label="Settings" className="flex gap-1 overflow-x-auto md:flex-col">
        {tabs.map((t) => (
          <a
            key={t.id}
            href={linkTo("settings", { tab: t.id })}
            aria-current={t.id === tab.id ? "page" : undefined}
            className={`rounded-md px-3 py-1.5 text-sm whitespace-nowrap transition-colors ${t.id === tab.id ? "bg-surface font-medium text-ink shadow-sm" : "text-muted hover:bg-surface hover:text-ink"}`}
          >
            {t.label}
            {t.admin && <span className="ml-1.5 text-xs text-muted">admin</span>}
          </a>
        ))}
      </nav>
      <section aria-label={tab.label} className="min-w-0 space-y-4">
        <div>
          <h2 className="text-lg font-semibold">{tab.label}</h2>
          {tab.hint && <p className="text-sm text-muted">{tab.hint}</p>}
        </div>
        <View session={session} onChanged={onSessionChanged} />
      </section>
    </div>
  );
}
