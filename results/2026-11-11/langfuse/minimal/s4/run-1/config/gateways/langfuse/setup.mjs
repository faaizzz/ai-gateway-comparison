// Headless setup for the Langfuse gateway stack (service langfuse-setup, run inside the
// Langfuse Web image so Node and the Prisma client are available). Plain Node, no packages
// beyond what the image ships.
//
// What it does, in order (the UI would do the same by hand):
//   1. waits for Web, logs in as the LANGFUSE_INIT_* owner user (NextAuth credentials flow);
//   2. sets the organisation's gateway config: ingestion project and ingestion mode
//      (NONE for the minimal profile, USAGE for prod, FULL for the S6 variant), through
//      the tRPC route aiGateway.updateConfig;
//   3. creates the provider connection to the mock (OpenAI, custom base URL). It first tries
//      the tRPC route aiGateway.createConnection. Web v4.78.0 refuses a custom base URL
//      outside development, preview and staging builds ("Custom base URLs and extra headers
//      are not available yet"); when that happens it inserts the same row with Prisma
//      (same encryption as Web, same columns) and says so. The data plane (resolve and the
//      gateway) has no such gate, so the connection works. Decision D22;
//   4. creates a gateway API key through aiGateway.createApiKey, then replaces its secret by
//      the fixed dummy value GATEWAY_KEY (Web stores only salted SHA-256 of the secret, the
//      same formula as createShaHash in packages/shared), so k6 can use a constant key. Web
//      does not let a caller choose the secret;
//   5. defines the mock's model price in the Langfuse project through the public API
//      (POST /api/public/models, project keys): 1 USD per million input tokens and 2 per
//      million output tokens, the same price every gateway gets (decision D19), so the
//      generations in prod get a cost;
//   6. writes READY_FILE (the container healthcheck looks for it) and idles, because
//      `docker compose up --wait` counts an exited container as a failure.
//
// Environment: WEB_URL, DATABASE_URL, SALT, ENCRYPTION_KEY, ORG_ID, PROJECT_ID, USER_EMAIL,
// USER_PASSWORD, INGESTION_MODE, CONNECTION_NAME, CONNECTION_BASE_URL, CONNECTION_CREDENTIAL,
// GATEWAY_KEY, PROJECT_PUBLIC_KEY, PROJECT_SECRET_KEY, READY_FILE.

import crypto from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";

const require = createRequire("/app/");
const { PrismaClient } = require("@prisma/client");

const e = (name) => {
  const v = process.env[name];
  if (!v) throw new Error(`missing environment variable ${name}`);
  return v;
};
const WEB = e("WEB_URL").replace(/\/$/, "");
const ORG_ID = e("ORG_ID");
const PROJECT_ID = e("PROJECT_ID");
const MODE = e("INGESTION_MODE");
const CONNECTION_NAME = e("CONNECTION_NAME");
const BASE_URL = e("CONNECTION_BASE_URL");
const CREDENTIAL = e("CONNECTION_CREDENTIAL");
const GATEWAY_KEY = e("GATEWAY_KEY");
const SALT = e("SALT");
const ENCRYPTION_KEY = e("ENCRYPTION_KEY");
const PROJECT_PK = e("PROJECT_PUBLIC_KEY");
const PROJECT_SK = e("PROJECT_SECRET_KEY");
const READY_FILE = process.env.READY_FILE || "/tmp/setup-ready";

const log = (msg) => console.log(`setup: ${msg}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- same formulas as Web (packages/shared) -----------------------------------------
const encrypt = (plain) => {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", Buffer.from(ENCRYPTION_KEY, "hex"), iv);
  const enc = cipher.update(plain, "utf8", "hex") + cipher.final("hex");
  return `${iv.toString("hex")}:${enc}:${cipher.getAuthTag().toString("hex")}`;
};
const displaySecret = (s) => `${s.slice(0, 6)}...${s.slice(-4)}`;
const fastHash = (secret) =>
  crypto
    .createHash("sha256")
    .update(secret)
    .update(crypto.createHash("sha256").update(SALT, "utf8").digest("hex"))
    .digest("hex");

// ---- HTTP with a tiny cookie jar ------------------------------------------------------
const jar = new Map();
const cookieHeader = () => [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
const storeCookies = (res) => {
  for (const line of res.headers.getSetCookie()) {
    const [pair] = line.split(";");
    const i = pair.indexOf("=");
    if (i > 0) jar.set(pair.slice(0, i).trim(), pair.slice(i + 1).trim());
  }
};
const http = async (path, init = {}) => {
  const res = await fetch(WEB + path, {
    redirect: "manual",
    ...init,
    headers: { ...(init.headers || {}), cookie: cookieHeader() },
  });
  storeCookies(res);
  return res;
};

async function waitForWeb() {
  for (let i = 0; i < 90; i++) {
    try {
      const r = await fetch(`${WEB}/api/public/health`);
      if (r.ok) return;
    } catch {
      /* not up yet */
    }
    await sleep(2000);
  }
  throw new Error("Web did not become healthy");
}

async function login() {
  const csrf = await (await http("/api/auth/csrf")).json();
  const body = new URLSearchParams({
    csrfToken: csrf.csrfToken,
    email: e("USER_EMAIL"),
    password: e("USER_PASSWORD"),
    callbackUrl: WEB,
    json: "true",
  });
  const res = await http("/api/auth/callback/credentials", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
  const session = await (await http("/api/auth/session")).json();
  if (!session?.user?.email) {
    throw new Error(`login failed (HTTP ${res.status}); session: ${JSON.stringify(session)}`);
  }
  log(`logged in as ${session.user.email}`);
}

// tRPC over HTTP, superjson envelope. Returns {ok, data | message}.
async function trpc(procedure, input) {
  const res = await http(`/api/trpc/${procedure}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ json: input }),
  });
  const text = await res.text();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { ok: false, message: `HTTP ${res.status}: ${text.slice(0, 300)}` };
  }
  if (parsed.error) {
    const err = parsed.error.json ?? parsed.error;
    return { ok: false, code: err?.data?.code, message: err?.message ?? JSON.stringify(err) };
  }
  return { ok: true, data: parsed.result?.data?.json };
}

async function main() {
  const prisma = new PrismaClient({ datasourceUrl: e("DATABASE_URL") });

  await waitForWeb();
  await login();

  // 2. gateway config: ingestion project and mode.
  let r = await trpc("aiGateway.updateConfig", {
    orgId: ORG_ID,
    defaultIngestionProjectId: PROJECT_ID,
    ingestionMode: MODE,
  });
  if (!r.ok) throw new Error(`updateConfig failed: ${r.message}`);
  log(`gateway config: project ${PROJECT_ID}, ingestion mode ${MODE}`);

  // 3. connection to the mock.
  const existing = await prisma.gatewayAiConnection.findFirst({
    where: { organizationId: ORG_ID, name: CONNECTION_NAME },
    select: { id: true },
  });
  if (existing) {
    log(`connection '${CONNECTION_NAME}' already exists (${existing.id})`);
  } else {
    r = await trpc("aiGateway.createConnection", {
      orgId: ORG_ID,
      name: CONNECTION_NAME,
      provider: "OPENAI",
      credential: CREDENTIAL,
      baseURL: BASE_URL,
    });
    if (r.ok) {
      log(`connection '${CONNECTION_NAME}' created through tRPC (custom base URL ${BASE_URL} accepted)`);
    } else {
      log(`tRPC createConnection refused: ${r.message}`);
      const row = await prisma.gatewayAiConnection.create({
        data: {
          organizationId: ORG_ID,
          name: CONNECTION_NAME,
          provider: "OPENAI",
          encryptedCredential: encrypt(CREDENTIAL),
          displaySecret: displaySecret(CREDENTIAL),
          baseURL: BASE_URL,
          status: "ENABLED",
        },
        select: { id: true },
      });
      log(`connection '${CONNECTION_NAME}' inserted with Prisma instead (${row.id}), base URL ${BASE_URL}`);
    }
  }

  // 4. gateway key with a fixed secret.
  const wantHash = fastHash(GATEWAY_KEY);
  const have = await prisma.apiKey.findFirst({
    where: { fastHashedSecretKey: wantHash },
    select: { id: true },
  });
  if (have) {
    log(`gateway key already in place (${have.id})`);
  } else {
    r = await trpc("aiGateway.createApiKey", {
      orgId: ORG_ID,
      name: "gwbench",
      metadata: {},
      issuedAs: "serviceAccount",
    });
    if (!r.ok) throw new Error(`createApiKey failed: ${r.message}`);
    const { id } = r.data;
    await prisma.apiKey.update({
      where: { id },
      data: { fastHashedSecretKey: wantHash, displaySecretKey: displaySecret(GATEWAY_KEY) },
    });
    log(`gateway key ${id} created through tRPC, secret replaced by the fixed local value`);
  }

  // 5. mock model price (D19): 1 USD per million input tokens, 2 per million output tokens.
  // The generation carries the requested name (gpt-mock) and the mock answers as mock-upstream.
  const modelRes = await fetch(`${WEB}/api/public/models`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Basic ${Buffer.from(`${PROJECT_PK}:${PROJECT_SK}`).toString("base64")}`,
    },
    body: JSON.stringify({
      modelName: "mock-upstream",
      matchPattern: "(?i)^(gpt-mock|mock-upstream)$",
      unit: "TOKENS",
      inputPrice: 0.000001,
      outputPrice: 0.000002,
    }),
  });
  if (modelRes.ok) log("model price defined: gpt-mock / mock-upstream, 1 and 2 USD per million tokens");
  else if (modelRes.status === 400 || modelRes.status === 409) log(`model price not created (HTTP ${modelRes.status}, probably exists): ${(await modelRes.text()).slice(0, 200)}`);
  else throw new Error(`creating the model price failed: HTTP ${modelRes.status} ${(await modelRes.text()).slice(0, 300)}`);

  await prisma.$disconnect();
  fs.writeFileSync(READY_FILE, "ok\n");
  log("ready");
  setInterval(() => {}, 3600 * 1000);
}

main().catch((err) => {
  console.error(`setup: FAILED: ${err?.stack || err}`);
  process.exit(1);
});
