ALTER TABLE galleries ADD COLUMN project_id INTEGER REFERENCES projects(id);
