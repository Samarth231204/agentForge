/**
 * The agentic flow's structure: nodes and the edges between them.
 *
 * This replaces the old `stages` array, which was really a derived schedule
 * pretending to be structure — it could express "these run together" but not
 * "this one waits specifically on that one", so there was nothing for a user
 * to rewire. Here the edges ARE the data: every node carries its own
 * `dependsOn`, execution order is computed from that, and the tree the
 * frontend draws is a picture of it rather than a separate idea.
 *
 * A node:
 *   { id, intent, task, dependsOn: [id], recipient?, repoUrl?, bodyFromNode? }
 */

/** Nodes of this intent must never run concurrently with each other. */
const SERIAL_INTENTS = new Set(["browse"]);

/**
 * The default wiring, applied to a freshly planned flow before the user
 * touches it: nodes sharing an intent are siblings (no edge between them, so
 * they run together), and a change of intent makes the new group wait on the
 * whole previous group.
 *
 * Grouping is on CONSECUTIVE runs of the same intent, not on the intent
 * globally — in [research, google, research] the two research nodes are
 * deliberately NOT siblings, because the google node sits between them and
 * merging them would reorder the work the planner laid out.
 *
 * `browse` is the exception the user asked for: browse nodes are chained to
 * each other so they queue up rather than opening several browsers at once.
 * The executor enforces this too, with a concurrency limit, so the rule still
 * holds if these edges are later rewired.
 */
export function assignDefaultEdges(nodes) {
  const wired = [];
  let previousGroup = [];
  let currentGroup = [];

  for (const node of nodes) {
    const previousNode = currentGroup[currentGroup.length - 1];
    const startsNewGroup = !previousNode || previousNode.intent !== node.intent;

    if (startsNewGroup) {
      if (currentGroup.length > 0) previousGroup = currentGroup;
      currentGroup = [];
    }

    // Wait on the previous intent group, plus — for a serial intent — on the
    // sibling immediately before, which is what keeps browsers queued.
    const dependsOn = [...previousGroup.map((n) => n.id)];
    if (SERIAL_INTENTS.has(node.intent) && currentGroup.length > 0) {
      dependsOn.push(currentGroup[currentGroup.length - 1].id);
    }

    const next = { ...node, dependsOn: [...new Set(dependsOn)] };
    currentGroup.push(next);
    wired.push(next);
  }

  return wired;
}

/**
 * Structural defects, phrased for a reader.
 *
 * The wording matters: these strings are fed straight back to the model by
 * the repair loop, so "node 4 depends on itself" is actionable in a way that
 * "invalid graph" is not.
 */
export function validateGraph(nodes) {
  const defects = [];
  const byId = new Map(nodes.map((n) => [n.id, n]));

  for (const node of nodes) {
    const deps = node.dependsOn || [];

    if (deps.includes(node.id)) defects.push(`Node ${node.id} depends on itself.`);

    for (const dep of deps) {
      if (!byId.has(dep)) defects.push(`Node ${node.id} depends on node ${dep}, which does not exist.`);
    }

    if (new Set(deps).size !== deps.length) defects.push(`Node ${node.id} lists the same dependency more than once.`);
  }

  for (const cycle of findCycles(nodes)) {
    defects.push(`These nodes form a cycle and can never run: ${cycle.join(" → ")}.`);
  }

  // A node can only send content produced by something it actually waits for.
  // Otherwise the body genuinely does not exist yet at the moment it runs,
  // which fails silently rather than loudly — the send just goes out composed
  // from scratch instead of carrying the text the user meant.
  for (const node of nodes) {
    if (node.bodyFromNode === undefined) continue;
    if (!byId.has(node.bodyFromNode)) {
      defects.push(`Node ${node.id} takes its body from node ${node.bodyFromNode}, which does not exist.`);
    } else if (!ancestorsOf(node.id, nodes).has(node.bodyFromNode)) {
      defects.push(
        `Node ${node.id} takes its body from node ${node.bodyFromNode}, but does not wait for it — add ${node.bodyFromNode} to node ${node.id}'s dependsOn.`
      );
    }
  }

  return defects;
}

/** Every node reachable by following dependsOn edges upwards. */
function ancestorsOf(id, nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const seen = new Set();
  const stack = [...(byId.get(id)?.dependsOn || [])];

  while (stack.length > 0) {
    const current = stack.pop();
    if (seen.has(current)) continue;
    seen.add(current);
    stack.push(...(byId.get(current)?.dependsOn || []));
  }
  return seen;
}

/**
 * Cycles, each reported as the path that closes it. Iterative DFS with a
 * colour marking, so a large graph can't blow the stack.
 */
function findCycles(nodes) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const state = new Map(); // id -> "open" | "closed"
  const cycles = [];
  const seenSignatures = new Set();

  const walk = (start) => {
    const path = [];
    const stack = [{ id: start, phase: "enter" }];

    while (stack.length > 0) {
      const frame = stack.pop();

      if (frame.phase === "exit") {
        state.set(frame.id, "closed");
        path.pop();
        continue;
      }

      if (state.get(frame.id) === "closed") continue;

      if (state.get(frame.id) === "open") {
        // Closed a loop — report the segment from the repeat onwards.
        const from = path.indexOf(frame.id);
        const cycle = [...path.slice(from), frame.id];
        const signature = [...cycle].sort((a, b) => a - b).join(",");
        if (!seenSignatures.has(signature)) {
          seenSignatures.add(signature);
          cycles.push(cycle);
        }
        continue;
      }

      state.set(frame.id, "open");
      path.push(frame.id);
      stack.push({ id: frame.id, phase: "exit" });
      for (const dep of byId.get(frame.id)?.dependsOn || []) {
        if (byId.has(dep)) stack.push({ id: dep, phase: "enter" });
      }
    }
  };

  for (const node of nodes) if (!state.has(node.id)) walk(node.id);
  return cycles;
}

/**
 * Nodes grouped into execution levels: everything in a level can run at the
 * same time, and each level waits for the one before it. Used both to run the
 * flow and to draw it — depth is the level, siblings share one.
 *
 * Any node left over after the levels are built is part of a cycle, and is
 * appended as a final level rather than dropped: losing a node silently is
 * worse than showing one that will fail, and validateGraph has already
 * reported the cycle by the time anyone sees this.
 */
export function toLevels(nodes) {
  const remaining = new Map(nodes.map((n) => [n.id, n]));
  const placed = new Set();
  const levels = [];

  while (remaining.size > 0) {
    const ready = [...remaining.values()].filter((node) =>
      (node.dependsOn || []).every((dep) => placed.has(dep) || !remaining.has(dep))
    );

    if (ready.length === 0) {
      levels.push([...remaining.values()]);
      break;
    }

    for (const node of ready) {
      remaining.delete(node.id);
    }
    for (const node of ready) placed.add(node.id);
    levels.push(ready);
  }

  return levels;
}

/**
 * Which nodes must run, given what a previous run already produced.
 *
 * This is what makes "supply the credential and press run again" cheap
 * instead of destructive. A node is dirty when it has no successful result
 * yet, or when its instruction or its dependencies changed since that
 * result was produced. Dirt then flows DOWNSTREAM: a node whose input is
 * about to be recomputed cannot keep an answer derived from the old input.
 *
 * Everything else is clean, keeps its stored result, and is skipped — which
 * is the difference between resuming a flow and re-sending every email it
 * already sent.
 */
export function dirtySet(nodes, previousNodes = [], results = {}) {
  const previousById = new Map(previousNodes.map((n) => [n.id, n]));
  const dirty = new Set();

  for (const node of nodes) {
    const before = previousById.get(node.id);
    const stored = results[node.id];
    const changed =
      !before ||
      before.task !== node.task ||
      before.intent !== node.intent ||
      JSON.stringify(before.dependsOn || []) !== JSON.stringify(node.dependsOn || []) ||
      before.bodyFromNode !== node.bodyFromNode ||
      before.recipient !== node.recipient ||
      before.repoUrl !== node.repoUrl;

    if (changed || !stored || stored.failed) dirty.add(node.id);
  }

  // Propagate to dependents until nothing new is marked.
  let grew = true;
  while (grew) {
    grew = false;
    for (const node of nodes) {
      if (dirty.has(node.id)) continue;
      if ((node.dependsOn || []).some((dep) => dirty.has(dep))) {
        dirty.add(node.id);
        grew = true;
      }
    }
  }

  return dirty;
}

export { SERIAL_INTENTS };
