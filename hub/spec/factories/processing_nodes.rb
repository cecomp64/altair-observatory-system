FactoryBot.define do
  factory :processing_node do
    sequence(:name) { |n| "altair-proc-#{n}" }
    active { true }
  end
end
