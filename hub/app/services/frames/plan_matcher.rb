module Frames
  # Frame -> exposure plan (§4.3): same target, same canonical filter, and
  # exposure within 0.5 s. Only lights count toward plans.
  module PlanMatcher
    module_function

    def match(frame)
      return nil unless frame.light? && frame.target && frame.filter && frame.exposure_s

      frame.target.exposure_plans.find do |plan|
        plan.filter == frame.filter && (plan.exposure_seconds - frame.exposure_s.to_f).abs <= ExposurePlan::EXPOSURE_MATCH_TOLERANCE_S
      end
    end
  end
end
