export type Agent = {
  id: string;
  name: string;
  created_at?: string;
  memory_blocks: string[];
};

export type Project = {
  name: string;
  path: string;
  branch?: string;
  commit?: string;
  subject?: string;
  ts?: string;
};
