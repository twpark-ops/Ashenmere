export interface Agent {
  id: string;
  name: string;
  title: string | null;
  bio: string | null;
  tier: string;
  status: string;
  balance: number;
  inventory: Record<string, number>;
  reputation: number;
  location: string;
  pos_x: number;
  pos_y: number;
  total_trades: number;
}

export interface WorldStatus {
  total_agents: number;
  active_agents: number;
  total_trades: number;
  tick: number;
  world_time: string;
  time_of_day: string;
  day: number;
}
