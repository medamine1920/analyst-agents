import { configure } from "@testing-library/react";

// Pages that render after a fetch can take more than the default second on a busy machine (the
// evaluations test failed that way twice); wait up to five before calling it a failure.
configure({ asyncUtilTimeout: 5000 });
