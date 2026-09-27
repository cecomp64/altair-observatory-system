FactoryBot.define do
  factory :astro_object do
    sequence(:primary_name) { |n| "Object #{n}" }
    ra_deg { 10.68471 }
    dec_deg { 41.26875 }
    object_type { "Galaxy" }
    source { "openngc" }

    # A member's own object: private to them unless shared.
    trait :custom do
      source { "custom" }
      created_by factory: :user
    end
  end
end
