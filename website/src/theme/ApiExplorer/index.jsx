import React from "react";
import CodeBlock from "@theme/CodeBlock";

const shellQuote = (value) => "'" + String(value).replaceAll("'", "'\\''") + "'";

export default function ApiExplorer({ item }) {
  const body = item.requestBody?.content ?? {};
  const path = item.path
    .replace("{model_id}", "kokoro-82m")
    .replace("{path}", "uploads/interview.wav");
  const url = "http://localhost:8000" + path;
  const args = ["curl --fail-with-body", "-X " + item.method.toUpperCase(), shellQuote(url)];

  const json = body["application/json"];
  const multipart = body["multipart/form-data"];
  if (json?.example !== undefined) {
    args.push("-H 'Content-Type: application/json'");
    args.push("--data " + shellQuote(JSON.stringify(json.example, null, 2)));
  } else if (multipart?.example) {
    for (const [field, value] of Object.entries(multipart.example)) {
      args.push("--form-string " + shellQuote(field + "=" + value));
    }
  } else if (body["application/octet-stream"]) {
    args.push("-H 'Content-Type: application/octet-stream'", "--data-binary @interview.wav");
  }

  return (
    <CodeBlock language="bash" title="curl">
      {args.join(" ")}
    </CodeBlock>
  );
}
