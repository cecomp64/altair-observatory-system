module Frames
  # A person assigns frames to a target in the Hub (§5.3 manual precedence):
  # the Hub records it as "manual", which Altair's upserts can't override,
  # and tells Altair with an assign_frames command so it re-plans.
  class Assigner
    def self.assign!(frames, target:, user:)
      frames = frames.to_a
      raise ArgumentError, "frames must be on #{target.telescope.name}" if frames.any? { |f| f.telescope_id != target.telescope_id }

      previous = frames.filter_map(&:target_id)
      Frame.transaction do
        frames.each do |frame|
          frame.target = target
          frame.project = target.project
          frame.assignment_source = "manual"
          frame.exposure_plan = PlanMatcher.match(frame)
          frame.save!
        end
        frames.group_by(&:processing_node).each do |node, node_frames|
          next unless node

          node.processing_commands.create!(kind: "assign_frames", target: target, requested_by: user,
                                           payload: { target_id: target.id, sha256s: node_frames.map(&:sha256) })
        end
      end
      ProgressRecomputeJob.debounce(previous + [ target.id ])
      frames.size
    end
  end
end
