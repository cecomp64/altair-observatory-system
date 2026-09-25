module Processing
  # Queues a command for every active processing node serving a telescope
  # (§5.4). Returns the created commands (none when no node serves it).
  class CommandIssuer
    def self.issue!(kind:, telescope:, payload:, requested_by: nil, target: nil)
      telescope.processing_nodes.active.map do |node|
        node.processing_commands.create!(kind: kind, payload: payload, requested_by: requested_by, target: target)
      end
    end
  end
end
