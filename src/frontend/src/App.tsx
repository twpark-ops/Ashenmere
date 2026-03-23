import { useWorldState, useAgents, useEvents, useTickStream } from './hooks/useGameState';
import { useState, useCallback } from 'react';

function TimeOfDayBadge({ time }: { time: string }) {
  const icons: Record<string, string> = {
    morning: '🌅', afternoon: '☀️', evening: '🌆', night: '🌙'
  };
  return <span className="text-lg">{icons[time] || '⏰'} {time}</span>;
}

function EventFeed({ events }: { events: any[] }) {
  if (events.length === 0) return <div className="text-gray-500 text-sm p-4">Waiting for events...</div>;
  return (
    <div className="space-y-2 max-h-96 overflow-y-auto">
      {events.map((e, i) => (
        <div key={e.id || i} className="bg-gray-800 rounded-lg p-3 text-sm">
          <div className="flex justify-between text-gray-400 text-xs mb-1">
            <span>Tick #{e.tick}</span>
            <span>{e.category}</span>
          </div>
          <div className="text-gray-100">{e.description}</div>
          {e.agent_name && <div className="text-indigo-400 text-xs mt-1">{e.agent_name}</div>}
        </div>
      ))}
    </div>
  );
}

function AgentCard({ agent }: { agent: any }) {
  const statusColor = agent.status === 'active' ? 'bg-green-500' : 'bg-gray-500';
  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <div className="flex items-center gap-2 mb-2">
        <div className={`w-2 h-2 rounded-full ${statusColor}`} />
        <span className="font-medium text-white">{agent.name}</span>
        {agent.title && <span className="text-gray-400 text-sm">({agent.title})</span>}
      </div>
      <div className="grid grid-cols-2 gap-2 text-sm text-gray-300">
        <div>Balance: <span className="text-green-400">${(agent.balance / 100).toFixed(0)}</span></div>
        <div>Rep: <span className="text-yellow-400">{agent.reputation}</span></div>
        <div className="col-span-2 text-gray-500">📍 {agent.location}</div>
      </div>
    </div>
  );
}

export function App() {
  const world = useWorldState();
  const agents = useAgents();
  const events = useEvents();
  const [lastTick, setLastTick] = useState(0);

  const wsConnected = useTickStream(useCallback((data: any) => {
    if (data.tick) setLastTick(data.tick);
  }, []));

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Header */}
      <header className="border-b border-gray-700 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">
            Agent<span className="text-indigo-400">Burg</span>
          </h1>
          <p className="text-gray-400 text-sm">AI agents living in a persistent world</p>
        </div>
        <div className="flex items-center gap-4">
          {world && <TimeOfDayBadge time={world.time_of_day || 'morning'} />}
          {world && <span className="bg-gray-800 px-3 py-1 rounded-full text-sm">Tick #{world.tick}</span>}
          <span className={`w-3 h-3 rounded-full ${wsConnected ? 'bg-green-500' : 'bg-red-500'}`}
                title={wsConnected ? 'Live' : 'Disconnected'} />
        </div>
      </header>

      <div className="flex h-[calc(100vh-73px)]">
        {/* Left: Event Timeline */}
        <div className="w-96 border-r border-gray-700 p-4 overflow-y-auto">
          <h2 className="text-lg font-semibold mb-4">Live Events</h2>
          <EventFeed events={events} />
        </div>

        {/* Center: World View (placeholder for PixiJS map) */}
        <div className="flex-1 flex items-center justify-center bg-gray-950">
          <div className="text-center text-gray-500">
            <div className="text-6xl mb-4">🌍</div>
            <div className="text-xl">World View</div>
            <div className="text-sm mt-2">
              {world ? `${world.active_agents} agents active · ${world.total_trades} trades` : 'Connecting...'}
            </div>
          </div>
        </div>

        {/* Right: Agent Cards */}
        <div className="w-80 border-l border-gray-700 p-4 overflow-y-auto">
          <h2 className="text-lg font-semibold mb-4">
            Agents <span className="text-gray-400 font-normal">({agents.length})</span>
          </h2>
          <div className="space-y-3">
            {agents.map(a => <AgentCard key={a.id} agent={a} />)}
            {agents.length === 0 && <div className="text-gray-500 text-sm">No agents yet</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
