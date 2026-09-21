import { expect, test } from "@playwright/test";
import type { Project, ProjectSummary } from "../src/types";

const email = process.env.REPLAY_E2E_EMAIL;
const password = process.env.REPLAY_E2E_PASSWORD;

test("real source: evidence search, version save, local edit preservation and conflict recovery", async ({
  page,
}, testInfo) => {
  test.skip(
    !email || !password,
    "Set REPLAY_E2E_EMAIL and REPLAY_E2E_PASSWORD for an existing local fixture account.",
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Good to see you." }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("01-sign-in.png"),
    fullPage: true,
  });
  await page.getByLabel("Email address", { exact: true }).fill(email!);
  await page.getByLabel("Password", { exact: true }).fill(password!);
  await page.getByRole("button", { name: "Open studio" }).click();
  await expect(page.locator(".projects-page")).toBeVisible();
  const identity = await (await page.request.get("/api/auth/me")).json();
  const headers = { "X-CSRF-Token": identity.csrf_token };
  const summaries: ProjectSummary[] = await (
    await page.request.get("/api/projects")
  ).json();
  let project: Project | undefined;
  for (const item of [...summaries].sort((a, b) =>
    a.created_at.localeCompare(b.created_at),
  )) {
    const candidate: Project = await (
      await page.request.get(`/api/projects/${item.id}`)
    ).json();
    if (
      candidate.assets.some((asset) => asset.status === "ready") &&
      candidate.role !== "viewer" &&
      candidate.timeline.version > 0
    ) {
      project = candidate;
      break;
    }
  }
  test.skip(
    !project,
    "This integration test needs a processed video and a saved timeline; run the real pipeline fixture first.",
  );
  const originalVersion = project!.timeline.version;
  const projectId = project!.id;
  const asset = project!.assets.find((item) => item.status === "ready")!;
  try {
    await page
      .locator(".project-nav button")
      .filter({ hasText: project!.name })
      .first()
      .click();
    await expect(
      page.getByRole("heading", { name: project!.name, exact: true }),
    ).toBeVisible();
    await page
      .locator(".asset-select")
      .filter({ hasText: asset.name })
      .first()
      .click();
    await expect(page.getByLabel("Search mode")).toBeVisible();
    await page.getByLabel("Search mode").selectOption("speech");
    await page.getByLabel("Describe a moment").fill("error");
    await page
      .getByRole("button", { name: "Search recording", exact: true })
      .click();
    await expect(page.locator(".evidence-card").first()).toBeVisible();
    await page
      .getByRole("button", { name: "Add moment 1 to timeline", exact: true })
      .click();
    await page
      .getByLabel("Clip title", { exact: true })
      .fill("Browser integration: verified source");
    await page
      .getByLabel("Clip caption")
      .fill("A sourced moment, preserved with its timing.");
    const saveResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/projects/${projectId}/timeline`) &&
        response.request().method() === "PUT",
    );
    await page
      .getByRole("button", { name: "Save version", exact: true })
      .click();
    expect((await saveResponse).status()).toBe(200);
    await expect(page.getByText(/Saved · v/)).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: testInfo.outputPath("02-edit-room.png"),
      fullPage: true,
    });

    // A collaborator saves a newer version while the current browser has dirty local text.
    // This exercises the real compare-and-swap endpoint and the polling path, with no API mocks.
    await page
      .getByLabel("Clip title", { exact: true })
      .fill("Unsaved local text must survive polling");
    const current: Project = await (
      await page.request.get(`/api/projects/${projectId}`)
    ).json();
    const timeline = current.timeline;
    const other = await page.request.put(
      `/api/projects/${projectId}/timeline`,
      {
        headers,
        data: {
          version: timeline.version,
          asset_id: timeline.asset_id,
          run_id: timeline.run_id,
          clips: timeline.clips.map((clip, index) =>
            index === 0
              ? { ...clip, title: "Another editor saved this version" }
              : clip,
          ),
        },
      },
    );
    expect(other.status()).toBe(200);
    await expect(
      page.getByText("A newer version is available.", { exact: false }),
    ).toBeVisible({ timeout: 15000 });
    await expect(page.getByLabel("Clip title", { exact: true })).toHaveValue(
      "Unsaved local text must survive polling",
    );
    const conflictResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/projects/${projectId}/timeline`) &&
        response.request().method() === "PUT",
    );
    await page
      .getByRole("button", { name: "Save version", exact: true })
      .click();
    expect((await conflictResponse).status()).toBe(409);
    await expect(
      page.getByRole("dialog", { name: "Two edits. Nothing lost." }),
    ).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath("03-version-conflict.png"),
      fullPage: true,
    });
    page.once("dialog", (dialog) => dialog.accept());
    await page
      .getByRole("button", { name: "Keep local edit for next save" })
      .click();
    await expect(page.getByLabel("Clip title", { exact: true })).toHaveValue(
      "Unsaved local text must survive polling",
    );
    const resolvedResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/projects/${projectId}/timeline`) &&
        response.request().method() === "PUT",
    );
    await page
      .getByRole("button", { name: "Save version", exact: true })
      .click();
    expect((await resolvedResponse).status()).toBe(200);
    await page.getByRole("button", { name: "Export", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Render saved version" }),
    ).toBeEnabled();
    await expect(
      page.getByRole("link", { name: "MP4", exact: true }).first(),
    ).toBeVisible();
    await page.getByRole("button", { name: "Close dialog" }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator(".sidebar")).toHaveCSS(
      "transform",
      "matrix(1, 0, 0, 1, -242, 0)",
    );
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: testInfo.outputPath("04-mobile-edit.png"),
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBeTruthy();
    expect(errors).toEqual([]);
  } finally {
    const latest: Project = await (
      await page.request.get(`/api/projects/${projectId}`)
    ).json();
    const response = await page.request.post(
      `/api/projects/${projectId}/timeline/restore`,
      {
        headers,
        data: {
          version: latest.timeline.version,
          target_version: originalVersion,
        },
      },
    );
    expect(
      response.ok(),
      `Restore the fixture's original edit: ${await response.text()}`,
    ).toBeTruthy();
  }
});

test("project creation and chunked binary upload use the live API", async ({
  page,
}, testInfo) => {
  test.skip(
    !email || !password || !process.env.REPLAY_E2E_VIDEO,
    "Set fixture account and REPLAY_E2E_VIDEO to exercise the real upload path.",
  );
  const login = await page.request.post("/api/auth/login", {
    data: { email, password },
  });
  expect(login.ok()).toBeTruthy();
  await page.goto("/");
  await page.getByRole("button", { name: "New project", exact: true }).click();
  const name = `Browser upload ${Date.now()}`;
  await page.getByLabel("Project name").fill(name);
  await page
    .getByRole("button", { name: "Create project", exact: true })
    .click();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await page
    .getByLabel("Choose a video to upload")
    .setInputFiles(process.env.REPLAY_E2E_VIDEO!);
  await expect(
    page.getByText("Upload complete. Analysis is queued."),
  ).toBeVisible({ timeout: 30000 });
  await expect(page.locator(".asset-item")).toHaveCount(1);
  await expect(
    page.getByText("Processing activity", { exact: false }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("05-real-upload.png"),
    fullPage: true,
  });
  const projects: ProjectSummary[] = await (
    await page.request.get("/api/projects")
  ).json();
  const project = projects.find((item) => item.name === name)!;
  const detail: Project = await (
    await page.request.get(`/api/projects/${project.id}`)
  ).json();
  expect(detail.assets).toHaveLength(1);
  expect(detail.jobs.length).toBeGreaterThan(0);
  const identity = await (await page.request.get("/api/auth/me")).json();
  // Deleting the disposable upload also fences its queued analysis tasks.
  const removed = await page.request.delete(
    `/api/assets/${detail.assets[0].id}`,
    { headers: { "X-CSRF-Token": identity.csrf_token } },
  );
  expect(removed.status()).toBe(204);
});

test("discarding a paused upload cancels server storage; a failed discard retains the resume handle", async ({
  page,
}) => {
  test.skip(
    !email || !password,
    "Set a local fixture account for the live cancellation API.",
  );
  const login = await page.request.post("/api/auth/login", {
    data: { email, password },
  });
  expect(login.ok()).toBeTruthy();
  const identity = await login.json();
  const headers = { "X-CSRF-Token": identity.csrf_token };
  const name = `Browser discard ${Date.now()}`;
  const created = await page.request.post("/api/projects", {
    headers,
    data: { name },
  });
  expect(created.status()).toBe(201);
  const project: ProjectSummary = await created.json();
  const started = await page.request.post(
    `/api/projects/${project.id}/uploads`,
    { headers, data: { filename: "unfinished.mp4", size: 8 } },
  );
  expect(started.status()).toBe(201);
  const upload = await started.json();
  const part = await page.request.put(`/api/uploads/${upload.id}?offset=0`, {
    headers: { ...headers, "Content-Type": "application/octet-stream" },
    data: Buffer.from("part"),
  });
  expect(part.status()).toBe(200);
  expect((await part.json()).offset).toBe(4);
  const saved = {
    id: upload.id,
    name: "unfinished.mp4",
    size: 8,
    modified: 0,
    fingerprint: "test-resume-metadata",
    chunkSize: upload.chunk_size,
  };
  await page.goto("/");
  await page.evaluate(
    ({ id, entry }) =>
      localStorage.setItem(`replay.upload.${id}`, JSON.stringify(entry)),
    { id: project.id, entry: saved },
  );
  await page.reload();
  await page.locator(".project-nav button").filter({ hasText: name }).click();
  await expect(
    page.getByRole("button", { name: "Discard upload", exact: true }),
  ).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  const discarded = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/uploads/${upload.id}`) &&
      response.request().method() === "DELETE",
  );
  await page
    .getByRole("button", { name: "Discard upload", exact: true })
    .click();
  expect((await discarded).status()).toBe(204);
  await expect(
    page.getByRole("button", { name: "Discard upload", exact: true }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(
      (id) => localStorage.getItem(`replay.upload.${id}`),
      project.id,
    ),
  ).toBeNull();
  expect(
    (await (await page.request.get(`/api/uploads/${upload.id}`)).json()).status,
  ).toBe("cancelled");
  const append = await page.request.put(`/api/uploads/${upload.id}?offset=4`, {
    headers: { ...headers, "Content-Type": "application/octet-stream" },
    data: Buffer.from("more"),
  });
  expect(append.status()).toBe(409);

  // A stale local handle receives a real 404, not a mocked network response.
  // It must remain available when the server has not confirmed cancellation.
  const stale = {
    ...saved,
    id: "ffffffffffffffffffffffffffffffff",
    name: "stale-source.mp4",
  };
  await page.evaluate(
    ({ id, entry }) =>
      localStorage.setItem(`replay.upload.${id}`, JSON.stringify(entry)),
    { id: project.id, entry: stale },
  );
  await page.reload();
  await page.locator(".project-nav button").filter({ hasText: name }).click();
  page.once("dialog", (dialog) => dialog.accept());
  const rejected = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/uploads/${stale.id}`) &&
      response.request().method() === "DELETE",
  );
  await page
    .getByRole("button", { name: "Discard upload", exact: true })
    .click();
  expect((await rejected).status()).toBe(404);
  await expect(
    page.getByText("Your resume information has been kept.", { exact: false }),
  ).toBeVisible();
  expect(
    JSON.parse(
      (await page.evaluate(
        (id) => localStorage.getItem(`replay.upload.${id}`),
        project.id,
      ))!,
    ),
  ).toEqual(stale);
  await page
    .getByLabel("Choose a video to upload")
    .setInputFiles({
      name: "different.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.from("new file"),
    });
  await expect(
    page.getByText("has an unfinished upload.", { exact: false }),
  ).toBeVisible();
  expect(
    JSON.parse(
      (await page.evaluate(
        (id) => localStorage.getItem(`replay.upload.${id}`),
        project.id,
      ))!,
    ),
  ).toEqual(stale);
});
