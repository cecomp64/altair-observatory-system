FactoryBot.define do
  factory :processing_job do
    processing_node
    sequence(:altair_id)
    kind { "NIGHT_STACK" }
    status { "succeeded" }
  end
end
