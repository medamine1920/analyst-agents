import { Component } from "react";

/**
 * Keeps one broken page from blanking the whole console: the sidebar stays, the page says what went wrong,
 * and moving to another page (a new `resetKey`) tries again.
 */
export default class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidUpdate(previous) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="space-y-2 rounded-xl border border-fail bg-fail-tint p-5 text-sm">
        <p className="font-medium text-fail">This page could not be shown.</p>
        <p className="text-muted">{String(this.state.error?.message ?? this.state.error)}</p>
        <button type="button" onClick={() => this.setState({ error: null })} className="rounded-md border border-rule bg-surface px-3 py-1 hover:border-ink">
          Try again
        </button>
      </div>
    );
  }
}
