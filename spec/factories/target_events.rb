FactoryBot.define do
  factory :target_event do
    target
    event_type { :progress }
    payload { {} }
  end
end
