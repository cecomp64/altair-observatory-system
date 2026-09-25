# Flags processing nodes that stopped sending heartbeats (§7.5), once per
# silence: the alert repeats only after the node has been heard from again.
class NodeHealthJob < ApplicationJob
  queue_as :default

  def perform
    ProcessingNode.active.find_each do |node|
      next if node.healthy?

      alerted_at = node.status["health_alerted_at"]
      next if alerted_at && node.last_heartbeat_at && Time.zone.parse(alerted_at) > node.last_heartbeat_at
      next if alerted_at && node.last_heartbeat_at.nil?

      node.update_columns(status: node.status.merge("health_alerted_at" => Time.current.iso8601))
      AdminAlertJob.perform_later(nil, node_id: node.id)
    end
  end
end
