-- Email templates library — reusable subject/body snippets for manual sends.
-- Powers the one-click template picker on proposal/contract/invoice send forms.
-- These are CONTENT only: Kevin still clicks Send. No automation, no auto-send
-- (Odysseus owns sequences). Merge fields resolve at render time against the
-- doc + project: {first_name} {client_name} {company} {project_title}
-- {doc_title} {doc_url} {site_name}
CREATE TABLE email_templates (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  subject    TEXT NOT NULL,
  body       TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  deleted_at TEXT
);

INSERT INTO email_templates (name, subject, body) VALUES
('Proposal ready',
 'Your proposal from {site_name}',
 'Hi {first_name},

Thanks so much for the chat about {project_title} — I''ve put together a proposal for you to review and accept online:

{doc_url}

Let me know if you''d like to adjust anything. Excited to work together!

Warmly,
{site_name}'),
('Contract to sign',
 'Contract for {project_title}',
 'Hi {first_name},

Here is the services agreement for {project_title}. You can read and sign it online here:

{doc_url}

Once it''s signed I''ll send over the invoice to lock in your date.

Thank you!
{site_name}'),
('Invoice / deposit due',
 'Invoice for {project_title}',
 'Hi {first_name},

Your invoice for {project_title} is ready. You can review the details and pay securely online here:

{doc_url}

The date is held once the deposit lands. Thanks so much!

{site_name}'),
('Gentle nudge',
 'Following up — {doc_title}',
 'Hi {first_name},

Just floating this back to the top of your inbox in case it slipped by:

{doc_url}

No rush at all — let me know if you have any questions.

{site_name}');
