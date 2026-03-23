export interface Agent {
  id: string;
  name: string;
  title: string | null;
  tier: string;
  status: string;
  balance: number;
  reputation: number;
  location: string;
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

export interface MarketPrice {
  item: string;
  price: number;
}

export interface TradeEvent {
  tick: number;
  item: string;
  price: number;
  quantity: number;
}
