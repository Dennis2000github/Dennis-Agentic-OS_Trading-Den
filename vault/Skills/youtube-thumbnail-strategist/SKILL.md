---
name: youtube-thumbnail-strategist
description: "Pre-production AI thumbnail strategist for the DENNIS Agent OS YouTube Studio Topic Backlog. Generates three curiosity-driven thumbnail concepts per proposed video topic (main image idea, headline, background, attention strategy, image-generation prompt) before production begins, generates actual 16:9 thumbnail images through the provider-independent image pipeline, scores concepts on a 100-point design rubric, checks title-thumbnail alignment and competitive differentiation, and persists concepts, approvals, and version history against each topic. Use when packaging new video topics, generating or comparing thumbnail concepts, regenerating or approving thumbnails, or auditing title-thumbnail alignment."
---

# SKILL: AI HIGH-CTR YOUTUBE THUMBNAIL STRATEGIST

## 1. OBJECTIVE

Build an AI-powered YouTube Thumbnail Strategy Skill integrated directly into the DENNIS Agent OS YouTube Studio Topic Backlog.

The skill must generate compelling, curiosity-driven YouTube thumbnail concepts for every proposed video topic BEFORE video production begins.

Its purpose is to improve the visual packaging of proposed videos through strong visual storytelling, readable headlines, clear composition, audience relevance, and competitive differentiation.

This is a pre-production intelligence skill, not merely an image generator.

## 2. INPUTS

Accept the following inputs:

- Topic or video idea
- Target YouTube niche
- Target audience
- Proposed video title
- Video angle and central promise
- Video hook or script outline, when available
- Channel branding and visual identity
- Reference thumbnails, optionally
- Competitor video references, when available

The skill must work even when only a topic idea is available.

Do not require a completed script before generating concepts.

## 3. THUMBNAIL CONCEPT GENERATION

Generate three distinct thumbnail concepts for each topic.

For each thumbnail concept, provide:

1. Main Image Idea
   - Identify the primary subject.
   - Define the main action, expression, object, or visual event.
   - Establish one unmistakable focal point.

2. Headline Text
   - Use very few words.
   - Prefer short, emotionally engaging phrases.
   - Avoid repeating the complete video title.
   - Make the headline readable at mobile thumbnail size.

3. Background Idea
   - Define the environment and setting.
   - Specify lighting, contrast, atmosphere, and color direction.
   - Ensure the background supports rather than competes with the focal subject.

4. Attention Strategy
   - Explain the curiosity gap.
   - Identify the emotional trigger.
   - Explain why the composition could attract attention.
   - Explain how the design differentiates itself from competing videos.

5. Image Generation Prompt
   - Produce a detailed image-generation prompt.
   - Specify subject, composition, camera angle, lighting, environment, color palette, and visual hierarchy.
   - Specify 16:9 landscape composition.
   - Leave sufficient space for readable headline placement.
   - Avoid unnecessary objects and visual clutter.

## 4. DESIGN RULES

Enforce the following rules:

- Use very little text.
- Communicate the main idea in less than one second.
- Focus on curiosity.
- Focus on one main visual idea.
- Avoid clutter.
- Use strong visual hierarchy.
- Use readable typography.
- Create contrast against competing video thumbnails.
- Maintain consistency with the channel's branding.
- Avoid misleading visual claims or promises that the video does not deliver.
- Never sacrifice content accuracy for clickbait.

Generate thumbnails in YouTube's 16:9 format, preferably 1280 × 720 pixels.

## 5. THUMBNAIL IMAGE GENERATION

Connect the skill to the existing DENNIS Agent OS image-generation provider architecture.

Requirements:

- Generate three actual thumbnail images, not just text descriptions.
- Keep image generation provider-independent.
- Allow users to switch image-generation providers.
- Allow regeneration of an individual concept without regenerating all concepts.
- Support reference images and channel brand assets.
- Support editable text overlays where the image-generation pipeline allows.
- Keep headline typography legible and independently editable.
- Save generated assets to the relevant YouTube Studio project.

Never treat a text-only concept as a completed thumbnail image.

## 6. TOPIC BACKLOG INTEGRATION

Add a Thumbnail Intelligence section to every Topic Backlog item.

Each backlog item must display:

- Proposed video title
- Generated thumbnail preview
- Thumbnail headline
- Thumbnail concept
- Planned video duration
- Target audience
- Thumbnail rationale
- Three alternative thumbnail concepts
- Topic priority
- Production status

The thumbnail preview must be visible directly on the topic card.

Clicking a topic opens a detailed Thumbnail Strategy panel.

Allow the user to:

- Preview all three concepts.
- Select the preferred thumbnail.
- Edit the headline.
- Regenerate an image.
- Modify the image-generation prompt.
- Compare concepts.
- Approve the thumbnail.
- Return to the concept-generation stage.

## 7. TITLE AND THUMBNAIL ALIGNMENT

The skill must evaluate the relationship between the proposed title and thumbnail.

The thumbnail must complement the title rather than duplicate it.

Example:

Video title:
"I Explored an Abandoned Mansion Nobody Has Entered in 50 Years"

Thumbnail headline:
"WHO WAS HERE?"

Visual concept:
A mysterious silhouette inside a dark abandoned mansion.

Evaluate whether the title and thumbnail communicate a consistent promise and create curiosity without misleading viewers.

## 8. COMPETITIVE DIFFERENTIATION

When competitor references or YouTube research are available:

- Identify recurring thumbnail styles in the niche.
- Analyze common headline patterns.
- Identify overused visual conventions.
- Find opportunities for visual differentiation.
- Avoid simply copying competitor thumbnails.

If competitor data is unavailable, generate concepts using niche conventions and clearly mark competitive analysis as unavailable.

## 9. QUALITY EVALUATION

Score each concept using a 100-point internal design evaluation:

- Instant visual comprehension: 25 points
- Curiosity and emotional appeal: 25 points
- Visual hierarchy and readability: 20 points
- Differentiation: 15 points
- Title and thumbnail alignment: 15 points

Return the score and explain weaknesses.

These scores are design heuristics, not predicted YouTube CTR or guaranteed performance.

## 10. SHARED INTELLIGENCE INTEGRATION

Use existing DENNIS Agent OS intelligence services where available:

- Oracle: Research topics and relevant factual context.
- Muse: Analyze audience interests and emotional appeal.
- Search OS: Research keywords and competing video packaging.
- Review OS: Evaluate thumbnail quality and title alignment.
- Shared Memory / Memory Galaxy: Retrieve channel branding, previous successful designs, and reusable preferences.

Do not create five separate user-facing workflows for this feature.

These should operate as shared intelligence capabilities behind the YouTube Studio interface.

## 11. DATA AND PERSISTENCE

Persist the following against each topic:

- Topic ID
- Thumbnail concept ID
- Concept description
- Headline
- Background direction
- Attention rationale
- Image-generation prompt
- Generated image asset reference
- Evaluation score
- Selected concept
- Approval status
- Version history
- Creation timestamp

Preserve the relationship between the topic, proposed title, planned duration, and thumbnail assets.

Do not lose approved concepts when the topic is moved into production.

## 12. ACCEPTANCE CRITERIA

The implementation is complete only when:

1. A new topic can generate thumbnail concepts without a finished script.
2. Three distinct concepts are generated per topic.
3. Each concept includes all four required strategy fields.
4. Actual thumbnail images can be generated and displayed.
5. Each thumbnail is visible inside its Topic Backlog card.
6. Proposed title and planned duration appear alongside the thumbnail.
7. Concepts can be edited, regenerated, compared, and approved independently.
8. Approved assets persist when the topic enters production.
9. The skill works with the existing image provider architecture.
10. Existing YouTube Studio workflows and backlog functionality remain intact.

## 13. IMPLEMENTATION SAFEGUARDS

Before changing code:

- Audit the existing YouTube Studio architecture.
- Inspect the Topic Backlog data model and components.
- Inspect the existing image-generation provider abstraction.
- Inspect the skill registry and execution system.
- Reuse existing components and services wherever possible.
- Identify missing functionality before implementing anything.

Do not rebuild the YouTube Studio dashboard.

Do not introduce unnecessary dependencies or duplicate image-generation infrastructure.

Implement this as a modular, reusable skill with backward-compatible changes.

First report the architecture audit and implementation plan. Then implement the smallest safe set of changes and test the complete topic-to-thumbnail-to-approval workflow.
