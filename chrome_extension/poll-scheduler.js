(function installPollScheduler(root) {
  // Each bridge has its own single-flight lane; long work in one cannot starve another.
  function singleFlight(operation) {
    let running = null;
    let pending = false;
    return function poll() {
      if (running) { pending = true; return running; }
      running = (async () => {
        do { pending = false; await operation(); } while (pending);
      })().finally(() => { running = null; });
      return running;
    };
  }
  root.PollScheduler = { singleFlight };
})(self);
